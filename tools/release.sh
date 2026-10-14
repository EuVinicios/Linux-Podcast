#!/usr/bin/env bash
# Publish a PodFlow release from this machine, without GitHub Actions.
#
#   tools/release.sh                 validate, test and build dist/ (.deb + snap)
#   tools/release.sh --publish       also send the snap to the Snap Store and create the
#                                    GitHub release v<versão> with the .deb, the snap and SHA256SUMS
#
# Options:
#   --channel <canal>   Snap Store channel for the upload (default: stable)
#   --skip-tests        skip `make test` (validation still runs)
#
# Needs: snapcraft logged in to the store (`snapcraft whoami`), LXD (`sudo snap install lxd &&
# sudo lxd init --auto && sudo usermod -aG lxd "$USER"`) and gh logged in (`gh auth status`).
# The snap is built for this machine's architecture; see docs/publicar-na-central-de-apps.md.
set -euo pipefail

cd "$(dirname "$0")/.."

PUBLISH=0
CHANNEL=stable
TESTS=1
while [ $# -gt 0 ]; do
    case "$1" in
        --publish) PUBLISH=1 ;;
        --channel) CHANNEL="$2"; shift ;;
        --skip-tests) TESTS=0 ;;
        -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Opção desconhecida: $1" >&2; exit 2 ;;
    esac
    shift
done

VERSION="$(make --no-print-directory version)"
TAG="v$VERSION"
ARCH="$(dpkg --print-architecture)"
DEB="dist/podflow_${VERSION}_all.deb"
SNAP="dist/podflow_${VERSION}_${ARCH}.snap"

step() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }

if [ "$PUBLISH" = 1 ]; then
    step "Conferindo o repositório para publicar $TAG"
    [ "$(git rev-parse --abbrev-ref HEAD)" = main ] || { echo "Publique a partir da main." >&2; exit 1; }
    [ -z "$(git status --porcelain)" ] || { echo "Há mudanças não commitadas." >&2; exit 1; }
    git fetch --quiet origin main
    [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] \
        || { echo "A main local difere de origin/main: faça push/pull antes." >&2; exit 1; }
    snapcraft whoami >/dev/null
    gh auth status >/dev/null
fi

step "Validando metadados ($VERSION)"
make --no-print-directory validate

if [ "$TESTS" = 1 ]; then
    step "Rodando os testes"
    make --no-print-directory test
fi

step "Gerando o .deb"
make --no-print-directory deb

step "Gerando o snap ($ARCH)"
rm -f "$SNAP"
if id -nG | grep -qw lxd; then
    snapcraft pack --output dist/
else
    # Right after `usermod -aG lxd` the group only applies to new logins.
    sg lxd -c "snapcraft pack --output dist/"
fi
[ -f "$SNAP" ] || { echo "Snap não encontrado em $SNAP" >&2; exit 1; }

(cd dist && sha256sum "$(basename "$DEB")" "$(basename "$SNAP")" > SHA256SUMS)
step "Pacotes prontos"
cat dist/SHA256SUMS

if [ "$PUBLISH" = 0 ]; then
    echo
    echo "Nada foi publicado. Para enviar à Snap Store e ao GitHub: tools/release.sh --publish"
    exit 0
fi

step "Enviando o snap à Snap Store (canal $CHANNEL)"
snapcraft upload --release="$CHANNEL" "$SNAP"

step "Criando a release $TAG no GitHub"
NOTES="$(mktemp)"
trap 'rm -f "$NOTES"' EXIT
awk -v v="$VERSION" '
    $0 ~ "^## \\[" v "\\]" {found=1; next}
    found && /^## \[/ {exit}
    found {print}
' CHANGELOG.md > "$NOTES"
cat >> "$NOTES" <<EOF

### Instalação

**Central de Aplicativos do Ubuntu:** procure por PodFlow ou use \`sudo snap install podflow\`.

**Ubuntu 26.04 LTS ou mais recente (.deb):**

\`\`\`bash
sudo apt install ./$(basename "$DEB")
\`\`\`

Confira a integridade dos arquivos com \`sha256sum -c SHA256SUMS\`.
EOF
if gh release view "$TAG" >/dev/null 2>&1; then
    gh release upload "$TAG" "$DEB" "$SNAP" dist/SHA256SUMS --clobber
    gh release edit "$TAG" --notes-file "$NOTES"
else
    gh release create "$TAG" "$DEB" "$SNAP" dist/SHA256SUMS --target main \
        --title "PodFlow $VERSION" --notes-file "$NOTES"
fi

step "Publicado"
snapcraft status podflow | head -n 12 || true
gh release view "$TAG" --json url --jq .url
