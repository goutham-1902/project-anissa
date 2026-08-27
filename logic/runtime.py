from __future__ import annotations


def resolve_effective_mode(settings: dict, control: dict) -> dict:
    runtime_mode=str(settings.get('mode') or '')
    workbook_mode=str(control.get('setup_mode') or '')
    authorized=settings.get('go_live_authorized') is True
    errors=[]
    if runtime_mode not in {'SETUP','LIVE'}:
        errors.append(f'unsupported runtime mode: {runtime_mode!r}')
    if workbook_mode not in {'SETUP','LIVE'}:
        errors.append(f'unsupported workbook mode: {workbook_mode!r}')
    if runtime_mode != workbook_mode:
        errors.append('runtime and workbook modes disagree')
    if runtime_mode == 'LIVE' and not authorized:
        errors.append('LIVE mode lacks go-live authorization')
    if runtime_mode == 'SETUP' and authorized:
        errors.append('SETUP mode cannot retain go-live authorization')
    effective='LIVE' if not errors and runtime_mode=='LIVE' and workbook_mode=='LIVE' and authorized else 'SETUP'
    return {
        'effective_mode':effective,'runtime_mode':runtime_mode,'workbook_mode':workbook_mode,
        'go_live_authorized':authorized,'ok':not errors,'errors':errors,
    }
