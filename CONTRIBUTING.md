# Como contribuir com o PodFlow

Obrigado pelo interesse! Relatos de problemas, ideias e código são bem-vindos.

## Relatando um problema

Abra uma [issue](https://github.com/EuVinicios/Linux-Podcast/issues) com:

- a versão (`podflow --version`) e a forma de instalação (snap, `.deb`, Flatpak ou código-fonte);
- a distribuição e a versão do GNOME;
- o que você fez, o que esperava e o que aconteceu;
- se possível, a saída de `podflow --debug` no terminal.

## Preparando o ambiente

```bash
sudo apt install git make python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-graphene-1.0 \
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gir1.2-secret-1 \
  gstreamer1.0-plugins-good gstreamer1.0-libav xvfb dbus-x11
git clone https://github.com/EuVinicios/Linux-Podcast.git
cd Linux-Podcast
make run
```

## Antes de abrir um pull request

1. `make test` precisa passar (os testes rodam sem internet e sem tocar no seu chaveiro).
2. `make validate` confere o `.desktop` e o metainfo.
3. Mudou algo visível? Rode `make screenshots` e confira as capturas em `data/screenshots/`.
4. Siga o estilo do código ao redor: textos da interface em português do Brasil, comentários
   em inglês, linhas de até 100 colunas.
5. Descreva a mudança no `CHANGELOG.md`, na seção da próxima versão.

## Organização do código

| Pasta | Conteúdo |
|---|---|
| `src/api/` | rankings da Apple, iTunes, parser RSS e o cliente de sincronização gPodder |
| `src/core/` | banco SQLite, player (GStreamer), fila, MPRIS, downloads, sincronização e chaveiro |
| `src/ui/` | barra lateral, telas, componentes, preferências e `style.css` |
| `tests/` | testes `unittest`, incluindo um servidor gPodder/Nextcloud falso |
| `website/` | site do projeto (GitHub Pages) |
| `snap/`, `build-aux/` | empacotamento snap, `.deb` e Flatpak |

Ao contribuir, você concorda em licenciar seu código sob a
[GPL-3.0-or-later](LICENSE).
