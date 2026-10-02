"""Pre-installed catalog of popular Brazilian podcasts.

It makes the very first launch useful even without internet: the shows are in
the library right away (with generated placeholder covers until the real
artwork is cached). Metadata checked against the iTunes API on 2026-10-01.

"Café da Manhã" (Folha) is a Spotify exclusive with no public feed, so the
daily "Boletim Folha" takes its place.
"""

SEED_VERSION = 1

SEED_PODCASTS: list[dict] = [
    {
        "itunes_id": 381816509,
        "title": "NerdCast",
        "author": "Jovem Nerd",
        "genre": "Comédia",
        "genre_id": 1303,
        "feed_url": "https://feeds.megaphone.fm/JNPD6227286900",
        "artwork_url": "https://is1-ssl.mzstatic.com/image/thumb/Podcasts211/v4/4c/7a/65/4c7a6515-85e9-cf2a-5342-5f11cfce9a0f/mza_17837230665868506995.jpg/600x600bb.jpg",
        "apple_url": "https://podcasts.apple.com/br/podcast/nerdcast/id381816509",
        "accent_color": "#7d4f2e",
        "description": "O podcast mais tradicional do Jovem Nerd: cultura pop, ciência, "
                       "história e muita conversa boa entre amigos, toda semana.",
    },
    {
        "itunes_id": 1533526944,
        "title": "Podpah",
        "author": "Podpah",
        "genre": "Entrevistas cômicas",
        "genre_id": 1496,
        "feed_url": "https://anchor.fm/s/57bd30d8/podcast/rss",
        "artwork_url": "https://is1-ssl.mzstatic.com/image/thumb/Podcasts126/v4/99/5f/91/995f9145-e24c-36c6-1666-6894a9343f58/mza_1935904832101931576.jpg/600x600bb.jpg",
        "apple_url": "https://podcasts.apple.com/br/podcast/podpah/id1533526944",
        "accent_color": "#de9a00",
        "description": "Igão e Mítico recebem convidados de todas as áreas em conversas "
                       "longas, descontraídas e cheias de humor.",
    },
    {
        "itunes_id": 1437955740,
        "title": "Flow Podcast",
        "author": "Flow",
        "genre": "Relacionamentos",
        "genre_id": 1544,
        "feed_url": "https://feeds.blubrry.com/feeds/flowpdc.xml",
        "artwork_url": "https://is1-ssl.mzstatic.com/image/thumb/Podcasts124/v4/d1/41/f7/d141f736-be11-e358-6ff4-fd841eef423e/mza_9849714184569769214.jpg/600x600bb.jpg",
        "apple_url": "https://podcasts.apple.com/br/podcast/flow-podcast/id1437955740",
        "accent_color": "#634e2b",
        "description": "Entrevistas longas e sem roteiro com personalidades brasileiras, "
                       "num dos formatos de bate-papo mais influentes do país.",
    },
    {
        "itunes_id": 1582015099,
        "title": "Boletim Folha",
        "author": "Folha de S.Paulo",
        "genre": "Notícias",
        "genre_id": 1489,
        "feed_url": "https://www.omnycontent.com/d/playlist/2f6a79aa-d181-48a4-92e0-ac5d00c8eb1d/3d46b2bc-0503-4d56-8c8a-ac5d0168cd1f/bd27a0a8-30b2-4cc8-b2e8-ac5d0168cd32/podcast.rss",
        "artwork_url": "https://is1-ssl.mzstatic.com/image/thumb/Podcasts115/v4/2d/0f/4f/2d0f4f0e-f7f4-9823-bcbd-2f314d411795/mza_7003418838553376207.jpg/600x600bb.jpg",
        "apple_url": "https://podcasts.apple.com/br/podcast/boletim-folha/id1582015099",
        "accent_color": "#098aba",
        "description": "As principais notícias do dia em poucos minutos, direto da "
                       "redação da Folha de S.Paulo.",
    },
    {
        "itunes_id": 1477406521,
        "title": "O Assunto",
        "author": "G1",
        "genre": "Notícias",
        "genre_id": 1489,
        "feed_url": "https://www.omnycontent.com/d/playlist/651a251e-06e1-47e0-9336-ac5a00f41628/04561b43-753d-4784-8e19-ac8b00e86411/3036d550-85b2-4301-b1e4-ac8b00e8641f/podcast.rss",
        "artwork_url": "https://is1-ssl.mzstatic.com/image/thumb/Podcasts122/v4/1a/07/7b/1a077b7a-9897-08fd-0b34-ca126757736b/mza_659742744913709222.jpg/600x600bb.jpg",
        "apple_url": "https://podcasts.apple.com/br/podcast/o-assunto/id1477406521",
        "accent_color": "#734b58",
        "description": "O podcast diário do g1 que aprofunda, de segunda a sexta, o tema "
                       "mais importante do noticiário com jornalistas e especialistas.",
    },
    {
        "itunes_id": 1583050574,
        "title": "Ciência Sem Fim",
        "author": "Estúdios Flow",
        "genre": "Ciência",
        "genre_id": 1533,
        "feed_url": "https://anchor.fm/s/d3eb3f24/podcast/rss",
        "artwork_url": "https://is1-ssl.mzstatic.com/image/thumb/Podcasts125/v4/d5/6d/c6/d56dc646-2a8a-77c5-d593-cae6c1d1dd6a/mza_477667617773183888.png/600x600bb.jpg",
        "apple_url": "https://podcasts.apple.com/br/podcast/ci%C3%AAncia-sem-fim/id1583050574",
        "accent_color": "#38495b",
        "description": "Conversas sobre ciência, tecnologia e o universo com "
                       "pesquisadores e divulgadores científicos.",
    },
    {
        "itunes_id": 1566207871,
        "title": "Inteligência Ltda.",
        "author": "Rogério Vilela",
        "genre": "Entrevistas cômicas",
        "genre_id": 1496,
        "feed_url": "https://anchor.fm/s/6cf4d5a0/podcast/rss",
        "artwork_url": "https://is1-ssl.mzstatic.com/image/thumb/Podcasts211/v4/41/8e/e8/418ee807-c149-96f6-bf81-c379538c1571/mza_9306777545034514338.jpg/600x600bb.jpg",
        "apple_url": "https://podcasts.apple.com/br/podcast/intelig%C3%AAncia-ltda/id1566207871",
        "accent_color": "#6e117d",
        "description": "Rogério Vilela conversa com cientistas, artistas, comediantes e "
                       "especialistas sobre os assuntos mais variados.",
    },
    {
        "itunes_id": 1802248733,
        "title": "Mano a Mano",
        "author": "Spotify Studios",
        "genre": "Sociedade e cultura",
        "genre_id": 1324,
        "feed_url": "https://feeds.megaphone.fm/GLT5279624272",
        "artwork_url": "https://is1-ssl.mzstatic.com/image/thumb/Podcasts211/v4/33/b1/59/33b15945-d602-36ee-901f-d64f26bc84d5/mza_14220226347400015450.jpeg/600x600bb.jpg",
        "apple_url": "https://podcasts.apple.com/br/podcast/mano-a-mano/id1802248733",
        "accent_color": "#604636",
        "description": "Mano Brown recebe convidados para conversas francas sobre música, "
                       "política, sociedade e trajetórias de vida.",
    },
    {
        "itunes_id": 156966105,
        "title": "Escriba Cafe - a história da humanidade",
        "author": "Christian Gurtner",
        "genre": "História",
        "genre_id": 1487,
        "feed_url": "https://api.substack.com/feed/podcast/555911/s/25883.rss",
        "artwork_url": "https://is1-ssl.mzstatic.com/image/thumb/Podcasts221/v4/87/bd/95/87bd9540-955b-495f-a85c-283fd4a6c92c/mza_7017571882842319240.jpeg/600x600bb.jpg",
        "apple_url": "https://podcasts.apple.com/br/podcast/escriba-cafe-a-hist%C3%B3ria-da-humanidade/id156966105",
        "accent_color": "#5f2f30",
        "description": "A história da humanidade em narrativas com roteiro e trilha "
                       "caprichados: mitos, religiões, impérios e grandes ideias.",
    },
    {
        "itunes_id": 1451605322,
        "title": "Escafandro",
        "author": "Rádio Escafandro",
        "genre": "Documentário",
        "genre_id": 1543,
        "feed_url": "https://anchor.fm/s/a9f2877c/podcast/rss",
        "artwork_url": "https://is1-ssl.mzstatic.com/image/thumb/Podcasts126/v4/1e/dd/9c/1edd9c5c-346f-8d65-4ea0-87bcf5d9dd39/mza_8389258683731400387.jpg/600x600bb.jpg",
        "apple_url": "https://podcasts.apple.com/br/podcast/escafandro/id1451605322",
        "accent_color": "#8a7e7e",
        "description": "Jornalismo narrativo em formato de documentário sonoro, "
                       "mergulhando fundo em histórias reais.",
    },
    {
        "itunes_id": 1553427360,
        "title": "Os Sócios Podcast",
        "author": "Grupo Primo",
        "genre": "Negócios",
        "genre_id": 1321,
        "feed_url": "https://anchor.fm/s/4c46462c/podcast/rss",
        "artwork_url": "https://is1-ssl.mzstatic.com/image/thumb/Podcasts221/v4/55/95/25/559525fe-0c69-1e3b-1225-6705a244b612/mza_2434104345682217915.jpg/600x600bb.jpg",
        "apple_url": "https://podcasts.apple.com/br/podcast/os-s%C3%B3cios-podcast/id1553427360",
        "accent_color": "#5c5232",
        "description": "Empreendedorismo, investimentos e carreira em bate-papos com "
                       "empresários e especialistas do mercado.",
    },
    {
        "itunes_id": 1492958937,
        "title": "Modus Operandi",
        "author": "Modus Operandi",
        "genre": "Crimes verídicos",
        "genre_id": 1488,
        "feed_url": "https://anchor.fm/s/101c83000/podcast/rss",
        "artwork_url": "https://is1-ssl.mzstatic.com/image/thumb/Podcasts221/v4/06/d8/0c/06d80c01-9805-92be-3014-15110a5fa23f/mza_15419489522249389235.jpeg/600x600bb.jpg",
        "apple_url": "https://podcasts.apple.com/br/podcast/modus-operandi/id1492958937",
        "accent_color": "#ba614e",
        "description": "Casos criminais reais contados em detalhes, do crime à "
                       "investigação e ao julgamento.",
    },
]
