import sqlite3

conn = sqlite3.connect("newsletter.db")
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS past_newsletters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    content TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

conn.commit()
conn.close()

print("✅ past_newsletters table created.")
