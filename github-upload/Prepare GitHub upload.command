#!/bin/zsh
# Double-click in Finder: refreshes the folder "github-upload" with exactly the files that belong
# on GitHub (no local settings, approvals, learned ranges, logs or Python environment) and opens it.
cd "$(dirname "$0")" || exit 1
rsync -a --delete --exclude-from=.gitignore --exclude=".git" --exclude=".claude" ./ github-upload/ || exit 1
open github-upload
