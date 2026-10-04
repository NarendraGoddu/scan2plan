"""Print each <div class="page"> separately and report how many PDF pages it needs.

A section that spills onto a second page is a section that will be cut in half
by the page break, which is how a field booklet ends up unreadable on site.

    python scripts/check_page_fit.py [docs/capture_protocol.html]
"""

import re
import subprocess
import pathlib
import time
import sys

target = sys.argv[1] if len(sys.argv) > 1 else 'docs/capture_protocol.html'
with open(target, encoding='utf-8') as fh:
    html = fh.read()
head = html[:html.index('<body>') + 6]
marker = '<div class="page">'
parts = html.split(marker)
pages = [marker + p for p in parts[1:]]
print('sections found:', len(pages))

edge = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
tmp = pathlib.Path('docs/_tmp')
tmp.mkdir(exist_ok=True)

for i, sec in enumerate(pages, 1):
    body = sec.split('</body>')[0]
    f = tmp / ('s%d.html' % i)
    f.write_text(head + body + '</body></html>', encoding='utf-8')
    out = str((tmp / ('s%d.pdf' % i)).resolve())
    subprocess.run([edge, '--headless', '--disable-gpu', '--no-pdf-header-footer',
                    '--print-to-pdf=' + out,
                    'file:///' + f.resolve().as_posix()],
                   capture_output=True)
    time.sleep(0.5)

time.sleep(3)
for i in range(1, len(pages) + 1):
    p = tmp / ('s%d.pdf' % i)
    if not p.exists():
        print('s%d: NO PDF' % i)
        continue
    d = p.read_bytes()
    n = len(re.findall(rb'/Type\s*/Page[^s]', d))
    print('section %d -> %d page(s) %s' % (i, n, '<-- OVERFLOWS' if n > 1 else ''))
