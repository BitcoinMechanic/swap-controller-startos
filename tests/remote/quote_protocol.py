"""Fixed mailbox stages shared by the disposable fixture and host launcher."""
QUOTE_STAGES = ('prepare', 'approve', 'status', 'review', 'worker',
                'worker-interrupt-resolution', 'supervisor-plan', 'verify-claim')
REPEAT_STAGES = ('repeat-setup', 'repeat-prepare', 'repeat-approve', 'repeat-worker')
REVERSE_STAGES = ('reverse-setup', 'reverse-prepare', 'reverse-approve', 'reverse-worker')
PILOT_STAGES = ('prepare', 'approve', 'status', 'worker', *REPEAT_STAGES, *REVERSE_STAGES)
BRIDGE_STAGES = (*QUOTE_STAGES, *REPEAT_STAGES, *REVERSE_STAGES, 'assert-controller-absent')


def validate_job(job, stages):
    if type(job) is not dict or set(job) != {'stage'} or job['stage'] not in stages:
        raise ValueError('invalid_quote_job')
    return job['stage']
