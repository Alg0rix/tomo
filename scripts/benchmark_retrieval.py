"""Small, deterministic lexical-recall benchmark using an isolated vault.

Run: .venv/bin/python scripts/benchmark_retrieval.py
Cross-language queries rely on the small ID/EN lexicon in read.py.
This is a regression fixture, not an estimate of production accuracy.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.runtime.memory.vault import read, write  # noqa: E402

# Expected page, or None for queries that should return no current facts.
CASES = [
    ('9000', 'server'),
    ('berapa port server?', 'server'),
    ('8000', None),
    ('beverage', 'profile'),
    ('favorite editor', 'profile'),
    ('friendbot', 'tomo'),
    ('runtime', 'tomo'),
    ('Ann?', 'ann'),
    ('jawaban ringkas', 'preferences'),
    ('alamat kantor', 'office'),
    ('What is my favorite pet?', None),
    ('apa yang di ini?', None),
    ('telescope', None),
    ('jawaban singkat', 'profile'),  # Cross-language via the small lexicon.
    # Added with the coverage gate and lexicon to check they generalize.
    ('port', 'server'),
    ('python', 'python'),
    ('ringkas', 'preferences'),
    ('concise', 'profile'),
    ('kopi', 'profile'),
    ('what does Ann work on', 'ann'),
    ('Rina', 'rina'),
    ('siapa istri saya', 'rina'),
    ('teman kuliah', 'budi'),
    ('tinggal dimana', 'home'),
    ('alergi', 'profile'),
    ('jadwal rapat', 'schedule'),
    ('meeting', 'schedule'),
    ('kucing', 'profile'),
    ('Mochi', 'profile'),
    ('favorite movie', None),
    ('nomor telepon', None),
    ('nama anjing', None),
    ('server database password', None),
    ('istri budi', None),
]


def benchmark() -> dict:
    with TemporaryDirectory(prefix='tomo-retrieval-bench-') as root:
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        opts = dict(home_root=Path(root), conn=conn)
        uid = 'benchmark'
        entries = [
            ('tool/server', 'Server listens on port 8000.'),
            ('user/profile', 'Favorite beverage: coffee.'),
            ('user/profile', 'Favorite editor: Vim.'),
            ('user/profile', 'The user prefers concise answers.'),
            ('tool/python', 'Python is the implementation language.'),
            ('project/tomo', 'Runtime uses [[tool/python]].'),
            ('person/ann', 'Works on finance.'),
            ('topic/mentions', 'Ann Ann Ann Ann Ann.'),
            ('topic/planning', 'Annual planning.'),
            ('topic/preferences', 'Untuk laporan, gunakan jawaban ringkas.'),
            ('place/office', 'Alamat kantor: Jalan Merdeka 10.'),
            ('person/rina', "Rina is the user's wife."),
            ('person/budi', 'Budi is a close friend from college.'),
            ('place/home', 'Lives in Bandung.'),
            ('topic/schedule', 'Weekly meeting every Monday at 10.'),
            ('user/profile', 'Allergic to peanuts.'),
            ('user/profile', 'Has a cat named Mochi.'),
        ]
        for key, text in entries:
            write.add_entity(uid, key, text, aliases=['friendbot'] if key == 'project/tomo' else [], **opts)
        write.add_entity(uid, 'tool/server', 'Server listens on port 9000.',
                         supersedes='Server listens on port 8000.', **opts)
        for number in range(30):
            write.add_entity(uid, f'topic/distractor-{number}',
                             f'The engine is running in area {number}.', **opts)
        latencies = []
        details = []
        recall = reciprocal_rank = negative_passes = 0
        for query, expected in CASES:
            start = time.perf_counter()
            hits = read.search(conn, uid, query, limit=5, home_root=Path(root))
            latencies.append((time.perf_counter() - start) * 1000)
            slugs = [h['slug'] for h in hits]
            if expected is None:
                passed = not hits
                negative_passes += int(passed)
            else:
                passed = expected in slugs
                recall += int(passed)
                if passed:
                    reciprocal_rank += 1 / (slugs.index(expected) + 1)
            details.append(dict(query=query, expected=expected, results=slugs, passed=passed))
        conn.close()
    positives = sum(expected is not None for _, expected in CASES)
    negatives = len(CASES) - positives
    ordered = sorted(latencies)
    return dict(
        cases=len(CASES), positive_queries=positives, negative_queries=negatives,
        recall_at_5=round(recall / positives, 3),
        mrr_at_5=round(reciprocal_rank / positives, 3),
        no_match_accuracy=round(negative_passes / negatives, 3),
        latency_p95_ms=round(ordered[min(len(ordered) - 1, int(len(ordered) * .95))], 2),
        details=details,
    )


if __name__ == '__main__':
    print(json.dumps(benchmark(), indent=2, ensure_ascii=False))
