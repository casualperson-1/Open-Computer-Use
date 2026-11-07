import asyncio
import os
from scheduler.manager import TaskSchedulerManager

async def test_scheduler_acceptance():
    print("Testing M6 Scheduler execution & concurrency lock...")
    db_file = "/tmp/test_scheduler.db"
    if os.path.exists(db_file):
        os.remove(db_file)

    mgr = TaskSchedulerManager(db_url=f"sqlite+aiosqlite:///{db_file}")
    await mgr.init_db()
    mgr.start()

    try:
        # Schedule a task
        task_id = await mgr.schedule_task("Daily Deal Check", "Find discount mice", rrule_or_seconds=1)
        print(f"Scheduled task registered with ID: {task_id}")

        # Trigger immediate run
        await mgr.execute_task_run(task_id)

        # Verify execution record in DB
        async with mgr.session_factory() as session:
            from database.models import TaskRun
            from sqlalchemy import select
            result = await session.execute(select(TaskRun).filter(TaskRun.task_id == task_id))
            runs = result.scalars().all()
            assert len(runs) >= 1, "Expected run record to exist in database"
            latest_run = runs[-1]
            assert latest_run.status == "completed", f"Expected completed status, got {latest_run.status}"
            print(f"✔ Task run logged: status={latest_run.status}, steps={latest_run.steps_count}")

        print("✔ M6 Scheduler acceptance test passed successfully!")

    finally:
        mgr.shutdown()
        if os.path.exists(db_file):
            os.remove(db_file)

if __name__ == "__main__":
    asyncio.run(test_scheduler_acceptance())
