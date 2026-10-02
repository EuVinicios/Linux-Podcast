# Changelog

Todas as mudanças relevantes do PodFlow são registradas aqui.
O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o projeto usa
[versionamento semântico](https://semver.org/lang/pt-BR/).

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

[0.1.0]: https://github.com/EuVinicios/Linux-Podcast/releases/tag/v0.1.0
