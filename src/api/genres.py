"""Apple Podcasts genre tree with Brazilian Portuguese names.

Generated from https://itunes.apple.com/WebObjects/MZStoreServices.woa/ws/genres?id=26&cc=br
Note: the legacy IDs 1311 (News) and 1315 (Science) are ignored by Apple's
charts; the current ones are 1489 and 1533.
"""

from __future__ import annotations

import unicodedata

TOP_GENRES: dict[int, str] = {
    1301: "Artes",
    1303: "Comédia",
    1304: "Educação",
    1305: "Crianças e família",
    1309: "Filme e TV",
    1310: "Música",
    1314: "Religião e espiritualidade",
    1318: "Tecnologia",
    1321: "Negócios",
    1324: "Sociedade e cultura",
    1483: "Ficção",
    1487: "História",
    1488: "True Crime",  # Apple BR: "Crimes verídicos"
    1489: "Notícias",
    1502: "Lazer",
    1511: "Governo",
    1512: "Saúde e fitness",
    1533: "Ciência",
    1545: "Esportes",
}

SUBGENRES: dict[int, tuple[str, int]] = {
    1306: ("Culinária", 1301),
    1402: ("Design", 1301),
    1405: ("Artes cênicas", 1301),
    1406: ("Artes visuais", 1301),
    1459: ("Moda e beleza", 1301),
    1482: ("Livros", 1301),
    1495: ("Improvisação", 1303),
    1496: ("Entrevistas cômicas", 1303),
    1497: ("Stand-up", 1303),
    1498: ("Aprenda um idioma", 1304),
    1499: ("Como fazer", 1304),
    1500: ("Autoajuda", 1304),
    1501: ("Cursos", 1304),
    1519: ("Educação para crianças", 1305),
    1520: ("Histórias para crianças", 1305),
    1521: ("Parentalidade", 1305),
    1522: ("Animais", 1305),
    1561: ("Avaliações de TV", 1309),
    1562: ("Nos bastidores", 1309),
    1563: ("Avaliações de filmes", 1309),
    1564: ("Filmes históricos", 1309),
    1565: ("Entrevistas de filmes", 1309),
    1523: ("Comentários sobre música", 1310),
    1524: ("História da música", 1310),
    1525: ("Entrevistas de música", 1310),
    1438: ("Budismo", 1314),
    1439: ("Cristianismo", 1314),
    1440: ("Islã", 1314),
    1441: ("Judaísmo", 1314),
    1444: ("Espiritualidade", 1314),
    1463: ("Hinduísmo", 1314),
    1532: ("Religião", 1314),
    1410: ("Carreiras", 1321),
    1412: ("Investimentos", 1321),
    1491: ("Administração", 1321),
    1492: ("Marketing", 1321),
    1493: ("Empreendedorismo", 1321),
    1494: ("Sem fins lucrativos", 1321),
    1302: ("Diários", 1324),
    1320: ("Viagens e lugares", 1324),
    1443: ("Filosofia", 1324),
    1543: ("Documentário", 1324),
    1544: ("Relacionamentos", 1324),
    1484: ("Drama", 1483),
    1485: ("Ficção científica", 1483),
    1486: ("Ficção cômica", 1483),
    1490: ("Notícias de negócios", 1489),
    1526: ("Notícias diárias", 1489),
    1527: ("Política", 1489),
    1528: ("Notícias de tecnologia", 1489),
    1529: ("Notícias esportivas", 1489),
    1530: ("Comentários de notícias", 1489),
    1531: ("Notícias de entretenimento", 1489),
    1503: ("Automotivo", 1502),
    1504: ("Aviação", 1502),
    1505: ("Hobbies", 1502),
    1506: ("Artesanato", 1502),
    1507: ("Jogos", 1502),
    1508: ("Casa e jardim", 1502),
    1509: ("Videogames", 1502),
    1510: ("Animação e mangá", 1502),
    1513: ("Saúde alternativa", 1512),
    1514: ("Boa forma", 1512),
    1515: ("Nutrição", 1512),
    1516: ("Sexualidade", 1512),
    1517: ("Saúde mental", 1512),
    1518: ("Medicina", 1512),
    1534: ("Ciências naturais", 1533),
    1535: ("Ciências sociais", 1533),
    1536: ("Matemática", 1533),
    1537: ("Natureza", 1533),
    1538: ("Astronomia", 1533),
    1539: ("Química", 1533),
    1540: ("Ciências da terra", 1533),
    1541: ("Ciências da vida", 1533),
    1542: ("Física", 1533),
    1546: ("Futebol", 1545),
    1547: ("Futebol americano", 1545),
    1548: ("Basquete", 1545),
    1549: ("Beisebol", 1545),
    1550: ("Hóquei", 1545),
    1551: ("Corrida", 1545),
    1552: ("Rugby", 1545),
    1553: ("Golfe", 1545),
    1554: ("Críquete", 1545),
    1555: ("Luta livre", 1545),
    1556: ("Tênis", 1545),
    1557: ("Voleibol", 1545),
    1558: ("Natação", 1545),
    1559: ("Mundo selvagem", 1545),
    1560: ("Esportes de fantasia", 1545),
}

# Curated shelves for Explore, in display order (spec IDs, corrected).
FEATURED_GENRES: list[int] = [1489, 1303, 1318, 1324, 1321, 1488, 1533, 1304]

# Categories offered by the Rankings filter.
CHART_GENRES: list[int] = FEATURED_GENRES + [1545, 1512, 1487, 1483, 1309, 1310,
                                             1301, 1305, 1502, 1314]

PODCASTS_ROOT_GENRE = 26


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").casefold().strip()
    return "".join(c for c in text if not unicodedata.combining(c))


_NAME_TO_TOP: dict[str, int] = {}
for _gid, _name in TOP_GENRES.items():
    _NAME_TO_TOP[_fold(_name)] = _gid
for _gid, (_name, _parent) in SUBGENRES.items():
    _NAME_TO_TOP[_fold(_name)] = _parent
_NAME_TO_TOP[_fold("Crimes verídicos")] = 1488
_NAME_TO_TOP[_fold("Podcasts")] = PODCASTS_ROOT_GENRE


def top_level(genre_id: int | None) -> int | None:
    """Map a (sub)genre ID to its top-level genre ID."""
    if genre_id is None:
        return None
    genre_id = int(genre_id)
    if genre_id in TOP_GENRES:
        return genre_id
    if genre_id in SUBGENRES:
        return SUBGENRES[genre_id][1]
    return None


def top_level_for_name(name: str | None) -> int | None:
    if not name:
        return None
    return _NAME_TO_TOP.get(_fold(name))


def genre_name(genre_id: int | None) -> str:
    if genre_id is None:
        return ""
    genre_id = int(genre_id)
    if genre_id in TOP_GENRES:
        return TOP_GENRES[genre_id]
    if genre_id in SUBGENRES:
        return SUBGENRES[genre_id][0]
    return ""
