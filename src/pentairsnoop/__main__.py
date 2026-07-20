"""Allow ``python -m pentairsnoop`` to invoke the CLI."""

from pentairsnoop.cli import main

raise SystemExit(main())
