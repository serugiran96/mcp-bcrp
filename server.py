"""Servidor MCP del BCRP. Empiecen aquí: buscar_series ya funciona; completen los TODO hasta que pasen las pruebas.

    uv run pytest                                           # el contrato: las 8 pruebas deben pasar
    uv run server.py                                        # el servidor, por stdio
    claude mcp add --scope project bcrp -- uv run server.py # conectarlo a Claude Code

Las cuentas las hace Python, no el modelo: así cada cifra sale de una llamada que se puede repetir.
"""
import re
import time
import unicodedata
from functools import cache
from pathlib import Path

import httpx
import pandas as pd
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

AQUI = Path(__file__).parent
CATALOGO = AQUI / "datos" / "catalogo_bcrp.csv"
URL_CATALOGO = "https://estadisticas.bcrp.gob.pe/estadisticas/series/metadata"
API = "https://estadisticas.bcrp.gob.pe/estadisticas/series/api"
SOLO_LECTURA = {"readOnlyHint": True, "openWorldHint": True}

mcp = FastMCP("bcrp", instructions=(
    "Series oficiales del Banco Central de Reserva del Perú (BCRP). Primero busca el código con buscar_series; "
    "luego usa resumen para variaciones, promedios y extremos, y serie para los datos. No calcules a mano: "
    "cita siempre el código de la serie y el periodo."))


def _sin_tildes(s):
    return unicodedata.normalize("NFD", str(s).lower()).encode("ascii", "ignore").decode()


@cache
def catalogo():
    """El catálogo oficial: 17 mil series en unos 7 MB. Se baja una vez y queda en datos/."""
    if not CATALOGO.exists():
        CATALOGO.parent.mkdir(exist_ok=True)
        r = httpx.get(URL_CATALOGO, timeout=120)
        r.raise_for_status()
        CATALOGO.write_text(r.content.decode("latin-1"), encoding="utf-8")
    df = pd.read_csv(CATALOGO, sep=";", dtype=str).fillna("")
    df = df.rename(columns={"Código de serie": "codigo", "Categoría de serie": "categoria", "Grupo de serie": "grupo",
                            "Nombre de serie": "nombre", "Frecuencia": "frecuencia",
                            "Fecha de actualización": "actualizada", "Fecha de inicio": "inicio"})
    df["texto"] = (df.categoria + " " + df.grupo + " " + df.nombre).map(_sin_tildes)
    return df


def _ficha(codigo):
    fila = catalogo().query("codigo == @codigo")
    if fila.empty:
        raise ToolError(f"No existe el código de serie {codigo!r}. Búsquenlo con buscar_series.")
    f = fila.iloc[0]
    return {"codigo": f.codigo, "nombre": f"{f.grupo} - {f.nombre}", "categoria": f.categoria,
            "frecuencia": f.frecuencia, "inicio": f.inicio, "actualizada": f.actualizada}


@mcp.tool(annotations=SOLO_LECTURA)
def buscar_series(texto: str, frecuencia: str | None = None, limite: int = 10) -> list[dict]:
    """Busca series del BCRP por palabras: todas deben aparecer en la categoría, el grupo o el nombre.

    Ejemplos: "ipc 12 meses", "tipo de cambio interbancario venta", "reservas internacionales netas",
    "tasa de referencia", "cobre precio". frecuencia: "Diaria", "Mensual", "Trimestral" o "Anual".
    Devuelve el código, el nombre completo, la frecuencia y desde cuándo hay datos. Si no hay resultados,
    prueben con menos palabras.
    """
    df = catalogo()
    m = pd.Series(True, index=df.index)
    for palabra in _sin_tildes(texto).split():
        m &= df.texto.str.contains(re.escape(palabra))
    if frecuencia:
        m &= df.frecuencia.str.lower() == frecuencia.lower()
    # primero las series vigentes y, entre ellas, las de nombre más corto (las más generales)
    sel = df[m]
    orden = sel.assign(vieja=sel.actualizada < "2025", largo=(sel.grupo + sel.nombre).str.len())
    return [_ficha(c) for c in orden.sort_values(["vieja", "largo"]).head(limite).codigo]


# ---------- TODO 1 · serie ----------
MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
         "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}


def _partes(fecha):
    """'2025', '2025-01', '2025-1', '2025-T1' o '2025-01-15' -> (año, mes, día, trimestre); lo que falte es None."""
    m = re.fullmatch(r"(\d{4})(?:-(?:[Tt]([1-4])|(\d{1,2}))(?:-(\d{1,2}))?)?", str(fecha).strip())
    if not m:
        raise ToolError(f"Fecha {fecha!r} no válida. Usen '2025', '2025-01', '2025-T1' o '2025-01-15'.")
    anio, trim, mes, dia = m.groups()
    return int(anio), int(mes) if mes else None, int(dia) if dia else None, int(trim) if trim else None


def _fecha_api(fecha, frecuencia, fin):
    """Convierte una fecha al formato que pide la API según la frecuencia. fin=True completa hacia el final."""
    anio, mes, dia, trim = _partes(fecha)
    f = frecuencia.lower()
    if f == "anual":
        return f"{anio}"
    if f == "trimestral":
        t = trim or ((mes - 1) // 3 + 1 if mes else (4 if fin else 1))
        return f"{anio}-{t}"
    if trim:                                           # un trimestre pedido para una serie mensual o diaria
        mes = 3 * trim if fin else 3 * trim - 2
    mes = mes or (12 if fin else 1)
    if f == "mensual":
        return f"{anio}-{mes}"
    dia = dia or (pd.Period(f"{anio}-{mes:02d}").days_in_month if fin else 1)
    return f"{anio}-{mes:02d}-{dia:02d}"


def _siglo(aa, hasta):
    """La API da años de dos cifras en series diarias y trimestrales: el siglo sale del rango pedido."""
    anio = 2000 + int(aa)
    return anio if anio <= hasta else anio - 100


def _periodo(nombre, hasta):
    """'Ene.2025' -> '2025-01', '01.Oct.26' -> '2026-10-01', 'T1.25' -> '2025-T1', '2025' -> '2025'."""
    n = nombre.strip()
    if m := re.fullmatch(r"(\d{1,2})\.(\w{3})\.(\d{2})", n):
        return f"{_siglo(m[3], hasta)}-{MESES[m[2].lower()]:02d}-{int(m[1]):02d}"
    if m := re.fullmatch(r"(\w{3})\.(\d{4})", n):
        return f"{m[2]}-{MESES[m[1].lower()]:02d}"
    if m := re.fullmatch(r"T([1-4])\.(\d{2})", n):
        return f"{_siglo(m[2], hasta)}-T{m[1]}"
    if re.fullmatch(r"\d{4}", n):
        return n
    raise ValueError(f"Periodo desconocido: {nombre!r}")


@cache
def _descargar(url):
    """Baja una URL de la API. Si el BCRP limita los pedidos responde HTML: espera y reintenta."""
    for intento in range(5):
        try:
            r = httpx.get(url, timeout=30, follow_redirects=True)
            if r.status_code == 200 and r.text.lstrip().startswith("{"):
                return r.json()
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(2 * (intento + 1))
    raise ToolError("El BCRP no respondió con datos. Esperen unos segundos y vuelvan a intentar.")


def _serie(codigo, desde, hasta):
    codigo = codigo.strip().upper()
    meta = _ficha(codigo)
    d, h = (_fecha_api(desde, meta["frecuencia"], False), _fecha_api(hasta, meta["frecuencia"], True))
    fuente = f"{API}/{codigo}/json/{d}/{h}"
    respuesta = _descargar(fuente)
    anio_hasta = _partes(hasta)[0]
    datos = []
    for p in respuesta.get("periods", []):
        valor = p["values"][0] if p.get("values") else "n.d."
        try:
            datos.append({"periodo": _periodo(p["name"], anio_hasta), "valor": round(float(valor), 4)})
        except ValueError:                             # "n.d." y similares: no hay dato ese periodo
            continue
    if not datos:
        raise ToolError(f"La serie {codigo} no tiene datos entre {desde} y {hasta}. Empieza en {meta['inicio']}.")
    return {"codigo": codigo, "nombre": meta["nombre"], "frecuencia": meta["frecuencia"],
            "unidad": respuesta.get("config", {}).get("title", ""), "datos": datos, "fuente": fuente}


@mcp.tool(annotations=SOLO_LECTURA)
def serie(codigo: str, desde: str, hasta: str) -> dict:
    """Datos de una serie del BCRP entre dos fechas, ambas incluidas. Úsala después de buscar_series.

    Fechas: "2025-01" (mensual), "2025-T1" (trimestral), "2025" (anual), "2026-10-15" (diaria).
    Para un solo dato, pongan la misma fecha en desde y hasta. Devuelve los periodos como "2025-01",
    "2026-10-01", "2025-T1" o "2025", sin los días sin dato, y la URL de la fuente para citarla.
    Ejemplo: serie("PN01273PM", "2026-01", "2026-09") -> inflación de Lima a 12 meses de enero a setiembre de 2026.
    """
    return _serie(codigo, desde, hasta)


# ---------- TODO 2 · resumen ----------
@mcp.tool(annotations=SOLO_LECTURA)
def resumen(codigo: str, desde: str, hasta: str) -> dict:
    """Primero, último, cambio, variación %, promedio, máximo y mínimo de una serie entre dos fechas.

    Úsenla en vez de hacer cuentas a mano: para "cuánto subió", "cuál fue el promedio" o "cuándo fue el máximo".
    Mismas fechas que serie. cambio = último - primero; variacion_pct = (último / primero - 1) × 100.
    Ojo: si la serie ya es una variación % (como la inflación), el cambio está en puntos porcentuales.
    """
    s = _serie(codigo, desde, hasta)
    df = pd.DataFrame(s["datos"])
    primero, ultimo = float(df.valor.iloc[0]), float(df.valor.iloc[-1])
    punto = lambda i: {"periodo": df.periodo[i], "valor": float(df.valor[i])}
    return {"codigo": s["codigo"], "nombre": s["nombre"], "frecuencia": s["frecuencia"], "n": len(df),
            "primero": punto(0), "ultimo": punto(len(df) - 1),
            "cambio": round(ultimo - primero, 4),
            "variacion_pct": round((ultimo / primero - 1) * 100, 4) if primero else None,
            "promedio": round(float(df.valor.mean()), 4),
            "maximo": punto(df.valor.idxmax()), "minimo": punto(df.valor.idxmin()),
            "fuente": s["fuente"]}


# ---------- TODO 3 · herramientas propias ----------
BANCO_MUNDIAL = "https://api.worldbank.org/v2/country"
INDICADORES = {"crecimiento_pbi": "NY.GDP.MKTP.KD.ZG", "inflacion": "FP.CPI.TOTL.ZG",
               "pbi_per_capita": "NY.GDP.PCAP.CD", "desempleo": "SL.UEM.TOTL.ZS",
               "exportaciones_pct_pbi": "NE.EXP.GNFS.ZS", "deuda_publica_pct_pbi": "GC.DOD.TOTL.GD.ZS"}


@mcp.tool(annotations=SOLO_LECTURA)
def comparar_paises(indicador: str = "crecimiento_pbi", paises: str = "PER,CHL,COL,MEX,BRA",
                    desde: int = 2015, hasta: int = 2025) -> dict:
    """Compara al Perú con otros países con datos anuales del Banco Mundial. El BCRP solo tiene datos del Perú:
    úsenla cuando la pregunta compara países ("¿creció Perú más que Chile?", "¿qué país tuvo más inflación?").

    indicador: crecimiento_pbi (%), inflacion (% anual, IPC), pbi_per_capita (US$ corrientes), desempleo (%),
    exportaciones_pct_pbi, deuda_publica_pct_pbi, o un código del Banco Mundial como "NY.GDP.MKTP.KD.ZG".
    paises: códigos ISO de 3 letras separados por comas, por ejemplo "PER,CHL,COL".
    Devuelve una tabla por año, el promedio de cada país en el periodo y la URL de la fuente.
    """
    codigo = INDICADORES.get(indicador.strip().lower(), indicador.strip())
    lista = [p.strip().upper() for p in re.split(r"[,; ]+", paises) if p.strip()]
    fuente = f"{BANCO_MUNDIAL}/{';'.join(lista)}/indicator/{codigo}?format=json&date={desde}:{hasta}&per_page=2000"
    try:
        r = httpx.get(fuente, timeout=30)
        cuerpo = r.json()
    except (httpx.HTTPError, ValueError):
        raise ToolError("El Banco Mundial no respondió. Vuelvan a intentar en unos segundos.")
    if len(cuerpo) < 2 or not cuerpo[1]:
        raise ToolError(f"Sin datos para {codigo!r} en {lista}. Revisen el indicador y los códigos ISO de 3 letras.")
    filas = [{"pais": x["countryiso3code"], "anio": int(x["date"]), "valor": round(x["value"], 4)}
             for x in cuerpo[1] if x["value"] is not None]
    df = pd.DataFrame(filas)
    tabla = df.pivot(index="anio", columns="pais", values="valor").sort_index()
    return {"indicador": cuerpo[1][0]["indicator"]["value"], "codigo": codigo,
            "por_anio": [{"anio": int(a), **{p: float(v) for p, v in fila.items() if pd.notna(v)}}
                         for a, fila in tabla.iterrows()],
            "promedio": {p: round(float(v), 4) for p, v in df.groupby("pais").valor.mean().items()},
            "fuente": fuente.replace("&per_page=2000", "")}


@mcp.tool(annotations=SOLO_LECTURA)
def correlacion(codigo_a: str, codigo_b: str, desde: str, hasta: str) -> dict:
    """Correlación de Pearson entre dos series del BCRP de la misma frecuencia, en los periodos que comparten.

    Úsenla para "¿se mueven juntos el cobre y el tipo de cambio?" o "¿la tasa de referencia sigue a la inflación?".
    Mismas fechas que serie. Va de -1 a 1; correlación no es causalidad.
    """
    a, b = _serie(codigo_a, desde, hasta), _serie(codigo_b, desde, hasta)
    df = pd.DataFrame(a["datos"]).merge(pd.DataFrame(b["datos"]), on="periodo", suffixes=("_a", "_b"))
    if len(df) < 3:
        raise ToolError("Hay menos de 3 periodos en común: revisen que las dos series tengan la misma frecuencia.")
    r = df.valor_a.corr(df.valor_b)
    return {"serie_a": {"codigo": a["codigo"], "nombre": a["nombre"]},
            "serie_b": {"codigo": b["codigo"], "nombre": b["nombre"]},
            "periodos": len(df), "desde": df.periodo.iloc[0], "hasta": df.periodo.iloc[-1],
            "correlacion": round(float(r), 4), "fuentes": [a["fuente"], b["fuente"]]}


# ---------- TODO 4 · recurso ----------
@mcp.resource("bcrp://serie/{codigo}")
def ficha(codigo: str) -> dict:
    """Ficha de una serie del BCRP: nombre, categoría, frecuencia, desde cuándo hay datos y última actualización."""
    return _ficha(codigo.strip().upper())


# ---------- TODO 5 · prompt ----------
@mcp.prompt
def informe(tema: str) -> str:
    """Informe corto sobre un tema económico del Perú, con cifras verificables del BCRP."""
    return (f"Escribe un informe de 5 viñetas sobre «{tema}» en el Perú con datos oficiales del BCRP.\n"
            "1. Busca las series con buscar_series.\n"
            "2. Usa resumen para variaciones, promedios y extremos; no hagas cuentas a mano.\n"
            "3. En cada viñeta cita el código de la serie y el periodo, por ejemplo (PN01273PM, 2026-09).\n"
            "4. Si comparas con otros países, usa comparar_paises y cita el Banco Mundial.\n"
            "Termina con una línea de fuentes con las URLs que devolvieron las herramientas.")


if __name__ == "__main__":
    mcp.run()
