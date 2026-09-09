import sqlite3
import csv
import os

from config.settings import load_settings

def generate_failed_jobs_report():
    settings = load_settings()
    db_path = settings.database_path

    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Query all jobs that did NOT successfully apply (FAILED or REQUIRES_MANUAL)
    query = """
        SELECT 
            j.title, 
            j.company, 
            a.status, 
            a.error_reason, 
            j.url
        FROM applications a
        JOIN jobs j ON a.job_id = j.id
        WHERE a.status IN ('FAILED', 'REQUIRES_MANUAL')
        ORDER BY a.applied_at DESC
    """
    
    cursor.execute(query)
    records = cursor.fetchall()

    if not records:
        print("\nGood news! There are no failed jobs in the database.\n")
        return

    report_path = "failed_jobs_report.csv"
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Job Title", "Company", "Status", "Reason", "URL"])
        writer.writerows(records)

    print(f"\n==============================================")
    print(f"          FAILED JOBS REPORT")
    print(f"==============================================\n")
    print(f"Found {len(records)} jobs that were skipped or failed.\n")
    
    for row in records[:10]:
        title, company, status, reason, url = row
        print(f"[{status}] {title} at {company}")
        print(f"   Reason: {reason}")
        print(f"   Link:   {url}\n")
    
    if len(records) > 10:
        print(f"... and {len(records) - 10} more.")

    print(f"\n=> Full detailed report saved to: {report_path}\n")

if __name__ == "__main__":
    generate_failed_jobs_report()
