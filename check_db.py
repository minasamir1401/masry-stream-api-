import sqlite3
import sys

sys.stdout.reconfigure(encoding='utf-8')
conn = sqlite3.connect('backend/data/masry_stream.db')
c = conn.cursor()

c.execute("SELECT id, title, media_type, parent_id FROM media WHERE media_type = 'SERIES'")
series_rows = c.fetchall()
print(f"Total SERIES: {len(series_rows)}")
for r in series_rows:
    c.execute("SELECT count(*) FROM media WHERE parent_id = ?", (r[0],))
    cnt = c.fetchone()[0]
    print(f"Series ID: {r[0]} | Title: {r[1]} | Episodes: {cnt}")

print("\n--- Any EPISODES with no parent or orphaned? ---")
c.execute("SELECT count(*) FROM media WHERE media_type = 'EPISODE' AND parent_id IS NULL")
print("Orphaned episodes:", c.fetchone()[0])

print("\n--- Sample movies ---")
c.execute("SELECT id, title, telegram_channel, duration, file_size FROM media WHERE media_type = 'MOVIE' LIMIT 5")
for m in c.fetchall():
    print(m)

