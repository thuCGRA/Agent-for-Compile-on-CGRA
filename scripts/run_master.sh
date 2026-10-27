#!/usr/bin/env bash
cd /home/steve/cac-armA
/home/steve/miniconda3/envs/cgra/bin/python scripts/master_table.py > work/master_table.log 2>&1
echo "rc=$?"
tail -40 work/master_table.log
