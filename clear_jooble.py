import sqlite3
import os

if os.path.exists('jobs.db'):
    conn = sqlite3.connect('jobs.db')
    conn.execute("DELETE FROM jobs WHERE source_name = 'jooble'")
    conn.commit()
    conn.close()
    print('Deleted Jooble jobs from DB')
