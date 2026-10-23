# Changelog

Todas as mudanças relevantes do PodFlow são registradas aqui.
O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o projeto usa
[versionamento semântico](https://semver.org/lang/pt-BR/).

## [0.4.0] - 2026-10-23

Layout mais próximo do Apple Podcasts para macOS, guiado por engenharia reversa (REA) do
Podcasts.app do macOS 27: strings em pt-BR, tipos das telas e o catálogo de cores do app.

### Alterado

- **Navegação com os nomes da Apple**: Início, Novidades, Top Charts e Buscar; na Biblioteca,
  Podcasts, Salvos, Downloads, **Últimos Episódios** (nova) e Reproduções Recentes.
- **Conta no rodapé do menu lateral**, com avatar, usuário e serviço de sincronização (ou
  “Entrar”), como o botão de perfil da Apple; recolhido, fica só o avatar.
- **Episódios no formato da Apple**: a data, o título e o resumo vêm seguidos de uma cápsula
  de reprodução com a duração ou o tempo restante (e uma barrinha de progresso), uma onda
  animada enquanto o episódio toca e o menu “…” na mesma linha.
- **Prateleiras**: o título vira “Título ›” para ver tudo (as de gênero abrem o Top Charts da
  categoria) e as setas de rolagem flutuam sobre as bordas, aparecendo ao passar o mouse.
- **Página do podcast**: o botão de reprodução continua o episódio em andamento (“Continuar”)
  e há ordenação “Mais Recentes Primeiro” / “Mais Antigos Primeiro”, lembrada por podcast.
- A segunda linha do player usa o formato da Apple: “NerdCast — Terça-feira”.
- No modo claro, o roxo de destaque é o AccentColor do Apple Podcasts (#7224D8).

### Corrigido

- As regras `@media (prefers-color-scheme: dark)` do app (sombras das capas) passam a valer no
  GTK 4.20+, que avalia essas consultas por provedor de CSS.
- No Libadwaita com o tema Yaru (Ubuntu), textos e ícones de destaque voltam a usar o roxo do
  PodFlow em vez da cor de destaque do sistema.

## [0.3.0] - 2026-10-13

### Alterado

- **Barra de reprodução no estilo do Apple Podcasts**: títulos longos ficam parados no começo,
  deslizam até uma cópia do texto ocupar o lugar e param de novo, com as bordas esmaecendo em
  vez de cortar letras no meio. O título rola enquanto o episódio toca e descansa quando está
  pausado; passar o mouse rola mesmo pausado, e a dica mostra o título inteiro.
- O título e o programa ganham uma coluna de verdade: os controles ficam centralizados com uma
  fração fixa da largura e as laterais dividem o resto (antes o título ficava com ~120 px).
- A segunda linha mostra o programa e a data do episódio (“NerdCast · 25 de set.”), e clicar
  nos títulos abre o programa, como já acontecia com a capa.
- Em janelas médias, velocidade, timer de sono e volume vão para o menu ⋯ em vez de serem
  cortados pela borda; no celular a barra vira um mini player com o título ocupando o espaço livre.
- Menu lateral recolhível só com ícones (<kbd>Ctrl</kbd>+<kbd>B</kbd> ou <kbd>F9</kbd>), lembrado entre
  as sessões.
- Os programas seguidos são atualizados direto pelo feed RSS ao abrir o app e em segundo plano.

### Distribuição

- Versões publicadas daqui mesmo com `tools/release.sh` (testes, `.deb`, snap, Snap Store e
  release do GitHub); os workflows do GitHub Actions passam a ser só manuais.

## [0.2.0] - 2026-10-02

### Adicionado

- **Conta e sincronização**: programas seguidos e progresso dos episódios sincronizados com
  gpodder.net, Nextcloud (app GPodder Sync, com login pelo navegador) ou qualquer servidor
  compatível com o gPodder (oPodSync, mygpo). Funciona junto com AntennaPod, gPodder e Kasts.
- A senha da sincronização fica no chaveiro do sistema (libsecret / portal de segredos).
- Pacote **snap** (`core26` + extensão GNOME) para a Central de Aplicativos do Ubuntu, com
  workflow que publica na Snap Store.
- **Site do projeto** no GitHub Pages, com política de privacidade.
- Preferência “Cor de destaque”: roxo do PodFlow ou a cor do sistema.

### Alterado

- Novo ícone: gradiente roxo, com o símbolo de play entre ondas de transmissão.
- Cor de destaque roxa por padrão, combinando com o ícone.
- Metainfo com site, perguntas frequentes, guia de contribuição e notas da versão.
- O lançador encontra o app ao lado do executável (instalações relocadas, como o snap).
- `make` sem alvo não abre mais o app; use `make run`.

## [0.1.0] - 2026-10-02

Primeira versão pública. 🎉

### Adicionado

- **Explorar**: carrossel de destaques com cores extraídas das capas, “Destaques da Semana”,
  “Em Alta” e prateleiras por gênero (Notícias, Comédia, Tecnologia, Sociedade e Cultura,
  Negócios, True Crime, Ciência e Educação).
- **Rankings**: Top Podcasts e Top Episódios do Brasil (1 a 50) com filtro por categoria.
- **Ouvir Agora**: Continuar Ouvindo, fila A Seguir e novos episódios dos programas seguidos.
- **Biblioteca**: programas seguidos com selo de novidades, episódios salvos, downloads e histórico.
- **Página do programa**: cabeçalho imersivo, descrição expansível, filtros Todos / Não ouvidos /
  Baixados e lista completa de episódios (iTunes + feed RSS).
- **Player**: barra persistente com −15 s / +30 s, velocidade de 0,5× a 2,5× sem alterar o tom,
  timer de sono (15/30/45/60 min ou fim do episódio), volume e fila retrátil com arrastar e soltar.
- Integração com o GNOME: controles de mídia e teclas multimídia (MPRIS v2), bloqueio de
  suspensão durante a reprodução, tema claro/escuro automático e notificações de download.
- Funcionamento offline: catálogo pré-instalado com 12 podcasts brasileiros, cache de rankings
  e capas, downloads de episódios.
- Busca no catálogo da Apple e adição de podcasts por URL de feed RSS.
- Pacotes `.deb` (Ubuntu 26.04+) e Flatpak (GNOME 50) gerados automaticamente no GitHub Actions.

[0.4.0]: https://github.com/EuVinicios/Linux-Podcast/releases/tag/v0.4.0
[0.3.0]: https://github.com/EuVinicios/Linux-Podcast/releases/tag/v0.3.0
[0.2.0]: https://github.com/EuVinicios/Linux-Podcast/releases/tag/v0.2.0
[0.1.0]: https://github.com/EuVinicios/Linux-Podcast/releases/tag/v0.1.0
