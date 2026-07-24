import sqlite3

p = r".runtime/system/system.db"
c = sqlite3.connect(p)
print("submissions", c.execute("select resource_type,status,count(*) from workflow_submissions group by resource_type,status order by resource_type,status").fetchall())
print("tasks", c.execute("select status,count(*) from workflow_tasks group by status order by status").fetchall())
print("approvals", c.execute("select count(*) from approval_records").fetchone()[0])
