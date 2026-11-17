from .celery_app import app


def job_stores():
    from .conversion_jobs import conversion_jobs
    from .agent_jobs import agent_jobs
    from .team_jobs import team_jobs
    from .plot_jobs import plot_jobs
    from .studio_jobs import studio_jobs

    return {
        store.kind: store
        for store in (conversion_jobs, agent_jobs, team_jobs, plot_jobs, studio_jobs)
    }


@app.task(name="story2script.execute_job", ignore_result=True)
def execute_job(kind, job_id, generation, deployment_id):
    from .redis_dispatch import database_identity

    if deployment_id != database_identity():
        raise ValueError("Web 与 Worker 的任务数据库标识不同，请检查配置。")
    store = job_stores().get(kind)
    if store is None:
        raise ValueError("不支持的任务类型。")
    store._execute(job_id, generation=generation, wait_for_lock=False)
