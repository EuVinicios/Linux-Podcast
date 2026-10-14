# Publicar o PodFlow na Central de Aplicativos do Ubuntu

A [Central de Aplicativos do Ubuntu](https://github.com/ubuntu/app-center) mostra os apps da
**Snap Store**. Um `.deb` aberto pelo arquivo aparece sempre como “Potencialmente inseguro”, sem
ícone, com “Editor desconhecido” e licença “unknown”: a Central monta essa página só com o que o
PackageKit informa sobre o arquivo (`packages/app_center/lib/deb/local_deb_model.dart`), então
não há metadado no `.deb` que mude isso. Publicado como snap, o PodFlow ganha página completa:
ícone, nome do editor, licença, capturas de tela, avaliações e atualizações automáticas.

O empacotamento já está pronto em [`snap/snapcraft.yaml`](../snap/snapcraft.yaml): base `core26`
(Ubuntu 26.04) com a extensão `gnome`, que traz GTK 4.22, Libadwaita, PyGObject, GStreamer e
Python 3.14 do runtime compartilhado `gnome-core26`. Como não há mais minutos no GitHub Actions,
o snap é gerado e enviado daqui mesmo com [`tools/release.sh`](../tools/release.sh) (seção 2).

## 1. Criar a conta de desenvolvedor (uma vez)

1. Crie uma conta Ubuntu One em <https://login.ubuntu.com> e entre em
   <https://snapcraft.io/account>. Aceite o contrato de desenvolvedor e escolha o nome público
   de editor (é o que aparece no lugar de “Editor desconhecido”).
2. Instale o Snapcraft: `sudo snap install snapcraft --classic`.
3. Registre o nome: `snapcraft login` e depois `snapcraft register podflow`.

## 2. Publicar daqui (sem GitHub Actions)

Prepare a máquina uma vez (pede a senha do `sudo`):

```bash
sudo snap install lxd
sudo lxd init --auto
sudo usermod -aG lxd "$USER"
sudo snap refresh snapcraft --channel latest/edge   # 9.2: extensão gnome com core26
snapcraft login                                      # se `snapcraft whoami` falhar
```

Depois, a cada versão (com a `main` atualizada no GitHub):

```bash
make publish                                     # canal stable
tools/release.sh --publish --channel candidate   # ou teste antes no candidate
```

O script valida o metainfo, roda os testes, gera o `.deb` e o snap em `dist/`, envia o snap à
loja e cria a release `v<versão>` no GitHub com o `.deb`, o snap e o `SHA256SUMS`. O snap sai
para a arquitetura da máquina (amd64); o arm64 fica na versão anterior até ser gerado num
computador ARM ou com `snapcraft remote-build` (compila de graça no Launchpad).

Para voltar uma versão na loja: `snapcraft release podflow <revisão anterior> stable`
(as revisões aparecem em `snapcraft revisions podflow`).

### Opcional: publicar pelo GitHub Actions

Se os minutos do Actions voltarem, gere uma credencial válida só para este snap:

```bash
snapcraft export-login --snaps=podflow \
  --acls package_access,package_push,package_update,package_release \
  --expires "2027-10-01" credenciais.txt
```

No GitHub, abra **Settings → Secrets and variables → Actions → New repository secret**, crie
`SNAPCRAFT_STORE_CREDENTIALS` e cole o conteúdo de `credenciais.txt`. Depois apague o arquivo.
Os workflows estão só com disparo manual; para voltar a publicar a cada push/tag, restaure os
gatilhos `push` em [`.github/workflows/snap.yml`](../.github/workflows/snap.yml).

Teste o canal `candidate` (`sudo snap install podflow --candidate`) e promova para o público
em <https://snapcraft.io/podflow/releases> ou com
`snapcraft promote podflow --from-channel candidate --to-channel stable`.

> **Snapcraft 9.2:** a extensão `gnome` com `core26` entrou no Snapcraft em 1º/10/2026 e chega
> ao canal estável na versão 9.2. Até lá use `latest/edge` (o workflow já faz isso).

## 3. Caprichar na página da loja

Em <https://snapcraft.io/podflow/listing>:

- **Capturas de tela:** envie as de `data/screenshots/` (explore, charts, podcast,
  listen-now, sync). As versões claras (`*-light.png`) ficam bonitas no tema padrão da Central.
- **Categoria:** “Música e áudio” (principal) e “Entretenimento”.
- **Banner:** 1218×240 px, opcional.
- **Links** e **licença** já vêm do `snapcraft.yaml`/metainfo.

## 4. Pedir conexões automáticas (recomendado)

A interface `password-manager-service` (guardar a senha da sincronização no chaveiro do GNOME)
não é conectada automaticamente. Sem ela o PodFlow funciona, mas guarda a senha num arquivo
privado dentro de `~/snap/podflow/`. Para conectá-la por padrão, abra um pedido no fórum:

1. <https://forum.snapcraft.io/c/store-requests/19> → **New Topic**.
2. Título: `Auto-connect password-manager-service for podflow`.
3. Explique: “PodFlow is a GNOME podcast player. It optionally syncs subscriptions with
   gpodder.net/Nextcloud and stores that password in the user's keyring via libsecret.”

Enquanto isso, o usuário pode conectar manualmente:
`sudo snap connect podflow:password-manager-service`.

## 5. Conferir na Central

Depois que a versão estiver em `stable`, ela aparece na busca da Central em algumas horas.
Para testar antes, instale pelo terminal (`sudo snap install podflow --edge`) e abra a Central:
a página do PodFlow já mostra o ícone, o editor e a licença.

## Verificação local

```bash
sudo snap install snapcraft --classic
sudo snap install lxd && sudo lxd init --auto
snapcraft pack                       # gera podflow_<versão>_<arquitetura>.snap
sudo snap install --dangerous ./podflow_*.snap
snap run podflow
```
