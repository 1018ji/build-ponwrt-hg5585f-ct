#!/usr/bin/env python3
"""Check NPU RX keeps its DMA owner when WED is initialized."""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    text = args.source.read_text()
    start = text.index('int mt7915_mmio_wed_init(')
    end = text.index('\n}\n', start) + 2
    function = text[start:end]
    assignment = '\tdev->mt76.dma_dev = wed->dev;'
    assert function.count(assignment) == 1
    guard = function.index('if (!dev->mt76.mmio.npu_legacy_rx)')
    assert guard < function.index(assignment)
    assert function[guard:function.index(assignment)+len(assignment)].count('dma_dev') == 1
    print('PASS: NPU RX keeps its DMA owner while native WED receives the WED DMA device')


if __name__ == '__main__':
    main()
