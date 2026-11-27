"""Check the mapper's own routes against the flow model's capacity rule.

Each ``parent:P`` group in routeInfo.log is one producer->consumer chain, so we
can recover the producer and ask: does any (x, y, t, port) carry values from two
*different* producers?  The hardware cannot do that -- one port carries one
value per cycle -- so a violation means my MRRG merged two distinct ports.
"""
import sys, os, re
sys.path.insert(0, 'tools/ilp')

HEAD_RE = re.compile(r'^node=(\d+),mapped=')
PARENT_RE = re.compile(r'^\s*parent:(\d+)')
PE_RE = re.compile(
    r'CGRA_Ins\.PE(?:_MEM)?_X(\d+)[|_]?_Y(\d+)[|_]?-T(\d+)\.([A-Za-z0-9_.]+)')


def parse_with_producer(path):
    """Return [(producer, consumer, [(x, y, t, port), ...]), ...]."""
    out = []
    consumer = None
    producer = None
    chain = None
    with open(path) as fh:
        for line in fh:
            m = HEAD_RE.match(line)
            if m:
                if chain:
                    out.append((producer, consumer, chain))
                consumer = int(m.group(1))
                producer = None
                chain = None
                continue
            m = PARENT_RE.match(line)
            if m:
                if chain:
                    out.append((producer, consumer, chain))
                producer = int(m.group(1))
                chain = []
                continue
            m = PE_RE.search(line)
            if m and chain is not None:
                chain.append((int(m.group(1)), int(m.group(2)), int(m.group(3)),
                              m.group(4)))
    if chain:
        out.append((producer, consumer, chain))
    return out


KERNELS = sys.argv[1:] or ['array_add', 'mac', 'fir', 'conv2', 'trmm']
for k in KERNELS:
    d = 'out/%s/m0' % k
    if not os.path.isdir(d):
        print(k, 'no baseline'); continue
    logs = [f for f in os.listdir(d) if f.endswith('routeInfo.log')]
    if not logs:
        print(k, 'no routeInfo'); continue
    logs.sort(key=lambda f: -os.path.getsize(os.path.join(d, f)))
    chains = parse_with_producer(os.path.join(d, logs[0]))
    users = {}
    for prod, _cons, chain in chains:
        for node in chain:
            users.setdefault(node, set()).add(prod)
    bad = {n: p for n, p in users.items() if len(p) > 1}
    single = [c for _p, _c, c in chains if len(c) == 1]
    print('%-10s chains=%-3d ports=%-4d multi-producer=%-3d single-port=%-3d'
          % (k, len(chains), len(users), len(bad), len(single)))
    for n, p in list(bad.items())[:8]:
        print('      ', n, '<- producers', sorted(p))
