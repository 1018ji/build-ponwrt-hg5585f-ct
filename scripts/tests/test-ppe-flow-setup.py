#!/usr/bin/env python3
"""Execute the real diagnostic wrapper and verify driver errors propagate."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile


def function(text, name):
    start = text.index('static int '+name+'(')
    brace = text.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    args = parser.parse_args()
    driver = args.kernel/'drivers/net/ethernet/airoha'
    header = (driver/'airoha_eth.h').read_text()
    stages = re.search(r'enum airoha_ppe_flow_stage \{.*?\n\};', header, re.S)[0]
    diag = re.search(r'struct airoha_ppe_flow_diag \{.*?\n\};', header, re.S)[0]
    wrapper = function((driver/'airoha_ppe.c').read_text(),
                       'airoha_ppe_flow_offload_replace')
    c = '''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
typedef uint64_t u64;
typedef uint16_t u16;
typedef long long atomic64_t;
#define IFNAMSIZ 16
#define lockdep_assert_held(x) ((void)0)
'''+stages+'\n'+diag+'''
struct airoha_ppe { struct airoha_ppe_flow_diag flow_diag; };
struct airoha_eth { struct airoha_ppe *ppe; };
struct flow_cls_offload { int unused; };
static int result, reject_stage;
static int __airoha_ppe_flow_offload_replace(struct airoha_eth *eth,
                                            struct flow_cls_offload *f)
{
    (void)f;
    eth->ppe->flow_diag.last_stage = reject_stage;
    return result;
}
'''+wrapper+'''
int main(void)
{
    struct airoha_ppe ppe = {0};
    struct airoha_eth eth = { .ppe = &ppe };
    struct flow_cls_offload flow = {0};
    const int errors[] = {-EOPNOTSUPP, -EINVAL, -ENOMEM, -EBUSY};
    unsigned int calls = 0;
    for (int stage = 0; stage < AIROHA_FLOW_COMPLETE; stage++) {
        reject_stage = stage;
        for (unsigned int e = 0; e < sizeof(errors)/sizeof(errors[0]); e++) {
            result = errors[e];
            ppe.flow_diag.last_egress[0] = 'x';
            ppe.flow_diag.wdma_wed = true;
            ppe.flow_diag.wdma_wcid = 42;
            assert(airoha_ppe_flow_offload_replace(&eth, &flow) == result);
            calls++;
            assert(ppe.flow_diag.errors[stage] == e + 1);
            assert(ppe.flow_diag.last_error == result);
            assert(ppe.flow_diag.replace_success == 0);
            assert(ppe.flow_diag.last_egress[0] == 0);
            assert(!ppe.flow_diag.wdma_wed && !ppe.flow_diag.wdma_wcid);
        }
    }
    result = 0;
    reject_stage = AIROHA_FLOW_INSERT;
    assert(!airoha_ppe_flow_offload_replace(&eth, &flow));
    assert(ppe.flow_diag.replace_calls == calls + 1);
    assert(ppe.flow_diag.replace_success == 1);
    assert(ppe.flow_diag.last_stage == AIROHA_FLOW_COMPLETE);
    assert(!ppe.flow_diag.last_error);
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as temp:
        source, binary = Path(temp)/'test.c', Path(temp)/'test'
        source.write_text(c)
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        str(source), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
    print('PASS: real PPE diagnostic wrapper preserves errors at every setup stage and resets stale context')


if __name__ == '__main__':
    main()
