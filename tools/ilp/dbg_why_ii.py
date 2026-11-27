"""Why does handing time to the mapper inflate II?

At the same II, compare per-edge register hops (d) of:
  (a) the mapper's own placement+schedule (baseline routeInfo.log)
  (b) the ILP placement, routed minimally on the port-level MRRG
The mapper's scheduler only ever takes the *earliest* reachable phase, so if a
placement forces d close to II-1 the mapper has no slack left and must raise II.
"""
import sys, json, os, subprocess
sys.path.insert(0, 'tools/ilp')
import mrrg
import mrrg_router as R
import validate_mrrg as V

KERNELS = sys.argv[1:] or ['array_add', 'mac', 'fir', 'conv2', 'trmm']
II = 4


def regs_of(path):
    """number of transitions that advance the cycle"""
    return sum(1 for a, b in zip(path, path[1:]) if b[2] != a[2])


def baseline_regs(k):
    W = 'out/%s/m0' % k
    if not os.path.isdir(W):
        return None
    logs = [f for f in os.listdir(W) if f.endswith('routeInfo.log')]
    if not logs:
        return None
    logs.sort(key=lambda f: -os.path.getsize(os.path.join(W, f)))
    chains = V.parse_routeinfo(os.path.join(W, logs[0]))
    return [regs_of(c) for _node, c in chains if c]


print('%-10s %-24s %-24s %s' % ('kernel', 'mapper own', 'ILP placement', 'II-1 budget'))
for k in KERNELS:
    W = 'out/%s' % k
    if not os.path.exists(W + '/dfg.xml'):
        print(k, 'no dfg'); continue
    base = baseline_regs(k)
    os.makedirs('/tmp/dq', exist_ok=True)
    pins_t = '/tmp/dq/%s_pins_t' % k
    subprocess.run(['.venv/bin/python', 'tools/ilp/ilp_mapper.py',
                    '--dfg', W + '/dfg.xml', '--arch', W + '/arch.json',
                    '--ii', str(II), '--timing', 'bypass', '--route-cycles', '-1',
                    '--select-coloc', 'exactly', '--time-limit', '20', '-o', pins_t],
                   capture_output=True, text=True)
    if not os.path.exists(pins_t) or os.path.getsize(pins_t) == 0:
        print('%-10s %-24s %-24s %s' % (k, base, 'placement FAIL', '-'))
        continue
    out = '/tmp/dq/%s_routes.json' % k
    subprocess.run(['.venv/bin/python', 'tools/ilp/mrrg_router.py',
                    '--dfg', W + '/dfg.xml', '--arch', W + '/arch.json',
                    '--pins', pins_t, '--ii', str(II), '--slack', '1',
                    '--time-limit', '45', '-o', out],
                   capture_output=True, text=True)
    if not os.path.exists(out):
        print('%-10s %-24s %-24s %s' % (k, base, 'route FAIL', '-'))
        continue
    res = json.load(open(out))
    ilp = [regs_of(rt['path']) for rt in res['routes']]
    bmax = max(base) if base else '?'
    imax = max(ilp) if ilp else '?'
    print('%-10s max=%-3s sum=%-4s      max=%-3s sum=%-4s      %s' % (
        k, bmax, sum(base) if base else '?', imax, sum(ilp), 'max d <= %d' % (II - 1)))
