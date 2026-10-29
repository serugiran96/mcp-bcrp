"""Figura del informe: aciertos, errores y «no sé» por modo, a partir de resultados.csv.

    uv run grafico.py          # escribe grafico.png
"""
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

AQUI = Path(__file__).parent
COLORES = {"acierta": "#2E7D5B", "se equivoca": "#C8553D", "no sabe": "#B8B2A7"}

res = pd.read_csv(AQUI / "resultados.csv")
orden = [m for m in ["memoria", "web", "mcp"] if m in set(res.modo)]
tabla = (res.groupby("modo").resultado.value_counts(normalize=True).unstack(fill_value=0) * 100)
tabla = tabla.reindex(index=orden, columns=list(COLORES), fill_value=0)
segundos = res.groupby("modo").segundos.mean().reindex(orden)

fig, ax = plt.subplots(figsize=(8, 4.5))
izquierda = pd.Series(0.0, index=orden)
for resultado, color in COLORES.items():
    ax.barh(orden, tabla[resultado], left=izquierda, color=color, label=resultado)
    izquierda += tabla[resultado]
for i, m in enumerate(orden):
    ax.text(101, i, f"{tabla.loc[m, 'acierta']:.0f} % · {segundos[m]:.1f} s", va="center", fontsize=10)
ax.set_xlim(0, 125)
ax.set_xlabel("% de las preguntas")
ax.invert_yaxis()
ax.spines[["top", "right"]].set_visible(False)
ax.legend(ncol=3, loc="lower center", bbox_to_anchor=(0.45, -0.32), frameon=False)
ax.set_title(f"¿Acierta Claude (Haiku) cifras del BCRP? {res.id.nunique()} preguntas por modo", loc="left")
fig.tight_layout()
fig.savefig(AQUI / "grafico.png", dpi=150)
print("grafico.png listo")
