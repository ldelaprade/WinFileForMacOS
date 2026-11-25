import re
import sys
from pathlib import Path


def main():
    seen = set()
    print("Available targets:")
    for filename in sys.argv[1:]:
        for line in Path(filename).read_text(encoding="utf-8").splitlines():
            match = re.match(r"^([a-zA-Z0-9_.-]+):[^#]*##\s*(.*)", line)
            if match and match[1] not in seen:
                target, description = match.groups()
                seen.add(target)
                print(f"  {target:<24} {description}")


if __name__ == "__main__":
    main()
