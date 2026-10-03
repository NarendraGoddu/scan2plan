import re, subprocess, pathlib, time, sys

html = open('docs/capture_protocol.html', encoding='utf-8').read()
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