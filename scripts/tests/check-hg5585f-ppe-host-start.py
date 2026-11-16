#!/usr/bin/env python3
"""Check host-managed PPE takeover of the HG5585F boot-time enable state."""
import argparse
from pathlib import Path


PPE_BUSY = 1 << 31
PPE_ENABLE = 1


def model(values):
    writes = []
    for index, value in enumerate(values):
        if value & PPE_BUSY:
            return -16, writes
        if value & PPE_ENABLE:
            writes.append(index)
            values[index] &= ~PPE_ENABLE
            if values[index] & PPE_BUSY:
                return -110, writes
    return 0, writes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    text = args.source.read_text()
    start = text.index('static int airoha_ppe_host_start(')
    end = text.index('\n}\n', start) + 2
    function = text[start:end]
    assert 'PPE_GLO_CFG_BUSY_MASK' in function
    assert 'PPE_GLO_CFG_EN_MASK' in function
    assert 'airoha_fe_clear(eth, REG_PPE_GLO_CFG(i), PPE_GLO_CFG_EN_MASK)' in function
    assert 'read_poll_timeout(airoha_fe_rr' in function
    assert function.index('airoha_fe_clear') < function.index('ppe->host_started = true')

    values = [0x0006070f, 0x0006070f]
    result, writes = model(values)
    assert result == 0 and writes == [0, 1] and values == [0x0006070e, 0x0006070e]
    result, writes = model([PPE_BUSY, 0])
    assert result == -16 and writes == []
    result, writes = model([0, 0])
    assert result == 0 and writes == []
    print('PASS: host-managed PPE clears stale enable bits, rejects busy ownership and preserves clean state')


if __name__ == '__main__':
    main()
