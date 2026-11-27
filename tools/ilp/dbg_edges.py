"""Why does the router see more edges than the mapper logged?

routeInfo.log has one ``parent:P`` chain per routed DFG edge.  If the DFG yields
more edges than that, the router is asked to route an edge the mapper never did
-- and since it has no phase to honour, the whole flow model goes INFEASIBLE.
"""
import sys, os
sys.path.insert(0, 'tools/ilp')
import mrrg_router as R
import dbg_share as S

KERNELS = sys.argv[1:] or ['array_add', 'mac', 'fir', 'conv2', 'trmm']

for k in KERNELS:
    d = 'out/%s/m0' % k
    W = 'out/%s' % k
    if not os.path.isdir(d):
        print(k, 'no baseline'); continue
    logs = [f for f in os.listdir(d) if f.endswith('routeInfo.log')]
    logs.sort(key=lambda f: -os.path.getsize(os.path.join(d, f)))
    logged = set((p, c) for p, c, _ch in S.parse_with_producer(os.path.join(d, logs[0])))
    nodes = R.parse_dfg(W + '/dfg.xml')
    dfg = set()
    for n in nodes.values():
        for (vid, _ni, _npb, _t) in n.outs:
            if vid in nodes:
                dfg.add((n.idx, vid))
    print('%-10s DFG edges=%-3d logged=%-3d  only-in-DFG=%s'
          % (k, len(dfg), len(logged), sorted(dfg - logged)))
    miss = sorted(logged - dfg)
    if miss:
        print('           only-in-log:', miss)
