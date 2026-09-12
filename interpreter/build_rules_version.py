"""Record the provenance of the rules source and generated engine."""
import hashlib
import json
import re
from pathlib import Path


def main():
    source = Path('rules.txt').read_bytes()
    date = re.search(r'effective as of (.+?)\.', source.decode('utf-8-sig')).group(1)
    metadata = {
        'effective_date': date,
        'source_url': 'https://media.wizards.com/2026/downloads/MagicCompRules%2020260819.txt',
        'source_sha256': hashlib.sha256(source).hexdigest(),
        'engine_sha256': hashlib.sha256(Path('datalog/engine_rules.dl').read_bytes()).hexdigest(),
    }
    Path('datalog/rules_version.json').write_text(json.dumps(metadata, indent=2) + '\n')


if __name__ == '__main__':
    main()
