"""Portable replan regression checks against a synthetic workbook instance."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from logic.workbook_io import WorkbookGateway
from logic.workload import replan_preview
from project.environment import RELEASE_ROOT
from project.instance import initialize_instance


EVENT = {
    'Event Type': 'USER_INITIATED_SURGE',
    'Title': 'Synthetic schedule change',
    'Estimated Minutes': 30,
    'User Authorized': 'YES',
    'Consequential Approval': 'NO',
    'Status': 'ACTIVE',
}


class WorkloadReplanTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        environment = initialize_instance(RELEASE_ROOT, Path(self.temp.name) / 'instance')
        self.gateway = WorkbookGateway(environment=environment, allow_offline_migration=True)
        self.brain_path = environment.brain_path

    def tearDown(self):
        self.temp.cleanup()

    def seed(self, *, status='Not Started', task_id=None):
        row = {
            'Application ID': 'synthetic-app', 'Campaign': 'Long-term',
            'Category': 'Research', 'Task Title': 'Check synthetic programme eligibility',
            'Definition of Done': 'Record the official eligibility decision.',
            'Status': status, 'Priority': 'High', 'Assigned Date': '2026-11-19',
            'Due Date': '2026-11-20', 'Estimated Minutes': 60,
            'Working Link Unblock Action': 'Verify the official eligibility entrypoint.',
        }
        if task_id:
            row['Task ID'] = task_id
        _, resolved_id = self.gateway.upsert_task(row)
        return row, resolved_id

    @staticmethod
    def idless(row, **updates):
        return {key: value for key, value in {**row, **updates}.items() if key != 'Task ID'}

    def test_idless_consequential_changes_preview_and_reject_atomically(self):
        row, resolved_id = self.seed(status='Started')
        changes = [
            self.idless(row, Status='Cancelled'),
            self.idless(row, **{'Due Date': '2026-11-25'}),
            self.idless(row, Priority='Low'),
        ]
        for change in changes:
            with self.subTest(change=change):
                preview = replan_preview(self.gateway.rows('TASKS'), EVENT, [change])
                self.assertTrue(preview['requires_consequential_approval'])
                self.assertEqual(preview['consequential_task_ids'], [resolved_id])
                self.assertEqual(preview['affected_task_ids'], [resolved_id])
                before = sha256(self.brain_path.read_bytes()).digest()
                with self.assertRaisesRegex(ValueError, 'explicit approval'):
                    self.gateway.apply_workload_replan(EVENT, [change])
                self.assertEqual(sha256(self.brain_path.read_bytes()).digest(), before)
                self.assertEqual(self.gateway.rows('WORKLOAD_EVENTS'), [])

    def test_approval_updates_existing_row_and_keeps_link_metadata(self):
        row, resolved_id = self.seed(status='Started')
        change = self.idless(row, **{'Due Date': '2026-11-25'})
        result = self.gateway.apply_workload_replan(
            {**EVENT, 'Consequential Approval': 'YES'}, [change],
        )
        self.assertEqual(result['tasks'], [{'action': 'update', 'task_id': resolved_id}])
        stored = self.gateway.rows('TASKS')
        self.assertEqual(len(stored), 1)
        self.assertEqual(str(stored[0]['Due Date'])[:10], '2026-11-25')
        self.assertEqual(stored[0]['Definition of Done'], row['Definition of Done'])
        self.assertEqual(stored[0]['Working Link Unblock Action'], row['Working Link Unblock Action'])
        self.assertEqual(self.gateway.rows('WORKLOAD_EVENTS')[0]['Affected Task IDs'], resolved_id)

    def test_harmless_idless_update_has_one_effort_row(self):
        row, resolved_id = self.seed()
        change = self.idless(row, **{'Remaining Minutes': 30})
        preview = replan_preview(self.gateway.rows('TASKS'), EVENT, [change])
        self.assertFalse(preview['requires_consequential_approval'])
        self.assertEqual(preview['affected_task_ids'], [resolved_id])
        self.assertEqual(preview['open_minutes_before'], 60)
        self.assertEqual(preview['open_minutes_after'], 30)
        self.gateway.apply_workload_replan(EVENT, [change])
        self.assertEqual(len(self.gateway.rows('TASKS')), 1)
        self.assertEqual(self.gateway.rows('TASKS')[0]['Remaining Minutes'], 30)

    def test_same_batch_uses_evolving_state_and_one_affected_id(self):
        row, resolved_id = self.seed()
        identity = {key: row[key] for key in (
            'Application ID', 'Campaign', 'Category', 'Task Title',
        )}
        changes = [{**identity, 'Status': 'Started'}, {**identity, 'Priority': 'Low'}]
        preview = replan_preview(self.gateway.rows('TASKS'), EVENT, changes)
        self.assertEqual(preview['affected_task_ids'], [resolved_id])
        self.assertEqual(preview['consequential_task_ids'], [resolved_id])
        self.assertEqual(preview['open_minutes_after'], 60)
        with self.assertRaisesRegex(ValueError, 'explicit approval'):
            self.gateway.apply_workload_replan(EVENT, changes)
        result = self.gateway.apply_workload_replan(
            {**EVENT, 'Consequential Approval': 'YES'}, changes,
        )
        self.assertEqual([item['task_id'] for item in result['tasks']], [resolved_id, resolved_id])
        stored = self.gateway.rows('TASKS')
        self.assertEqual(len(stored), 1)
        self.assertEqual((stored[0]['Status'], stored[0]['Priority']), ('Started', 'Low'))
        self.assertEqual(self.gateway.rows('WORKLOAD_EVENTS')[0]['Affected Task IDs'], resolved_id)

    def test_ambiguous_idless_match_fails_closed_and_explicit_new_id_stays_new(self):
        row, _ = self.seed(task_id='T1')
        self.gateway.upsert('TASKS', 'Task ID', {**row, 'Task ID': 'T2'})
        change = self.idless(row, **{'Remaining Minutes': 30})
        with self.assertRaisesRegex(ValueError, 'multiple existing tasks'):
            replan_preview(self.gateway.rows('TASKS'), EVENT, [change])
        before = sha256(self.brain_path.read_bytes()).digest()
        with self.assertRaisesRegex(ValueError, 'multiple existing tasks'):
            self.gateway.apply_workload_replan(EVENT, [change])
        self.assertEqual(sha256(self.brain_path.read_bytes()).digest(), before)
        new = {**row, 'Task ID': 'T3', 'Task Title': 'Map synthetic lab equipment'}
        preview = replan_preview(self.gateway.rows('TASKS'), EVENT, [new])
        self.assertEqual(preview['affected_task_ids'], ['T3'])
        self.gateway.apply_workload_replan(EVENT, [new])
        self.assertEqual({item['Task ID'] for item in self.gateway.rows('TASKS')}, {'T1', 'T2', 'T3'})


if __name__ == '__main__':
    unittest.main()
