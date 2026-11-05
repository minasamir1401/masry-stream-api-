import urllib.request
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

req = urllib.request.Request('http://localhost:3000/series/30', headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req) as resp:
    html = resp.read().decode('utf-8')
    print('Status:', resp.status)
    ep_links = re.findall(r'href="/watch/(\d+)"', html)
    print('Total episode links on /series/30:', len(ep_links))
    print('Episode IDs:', ep_links[:5])

# Also test /series page
req2 = urllib.request.Request('http://localhost:3000/series', headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req2) as resp:
    html2 = resp.read().decode('utf-8')
    series_links = re.findall(r'href="/series/(\d+)"', html2)
    print('Total series links on /series:', len(series_links))
    print('Series IDs on /series:', series_links)

