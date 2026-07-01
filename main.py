from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import numpy as np
from datetime import datetime
import re
import io
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="API Análisis Asistencia Propar")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== CONSTANTES ==========

# Mapeo de departamento (columna del biométrico) → sucursal normalizada
DEPT_MAP = {
    "MATRIZ LIMPIEZA": "Matriz",
    "MATRIZ": "Matriz",
    "CIUDAD DEL ESTE": "Ciudad del Este (CDE)",
    "CORONEL OVIEDO": "Oviedo",
    "ENCARNACION": "Encarnación",
    "ENCARNACIÓN": "Encarnación",
    "HOHENAU": "Hohenau",
}

# ========== FUNCIONES HELPER ==========

def dept_to_sucursal(dept):
    """Convierte 'PROPAR S.R.L/CORONEL OVIEDO' → 'Oviedo'"""
    if pd.isna(dept) or not dept:
        return None
    d = str(dept).upper().replace("PROPAR S.R.L/", "").strip()
    # Orden importa: MATRIZ LIMPIEZA antes de MATRIZ
    for key in sorted(DEPT_MAP.keys(), key=len, reverse=True):
        if key in d:
            return DEPT_MAP[key]
    return d.title()

def parse_horario(h):
    """
    Extrae (inicio, fin) de strings como:
    'CO - HOHE - ENCAR(08:00:00-17:45:00)'
    'MATRIZ L - V(08:00:00-17:45:00)'
    'SABADO(08:00:00-12:15:00)'
    """
    if pd.isna(h) or not h or str(h).strip() in ["-", "", "—"]:
        return None, None
    m = re.search(r"\((\d{1,2}:\d{2}(?::\d{2})?)-(\d{1,2}:\d{2}(?::\d{2})?)\)", str(h))
    if m:
        return coerce_time(m.group(1)), coerce_time(m.group(2))
    return None, None

def coerce_time(s):
    if pd.isna(s) or not s:
        return None
    t = str(s).strip()
    if t in ["", "-", "—", "–", "null", "None"]:
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", t)
    if not m:
        return None
    hh, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
        return None
    return f"{hh:02d}:{mm:02d}:{ss:02d}"

def fmt_ddmmyyyy(d):
    if pd.isna(d) or not d:
        return None
    if isinstance(d, datetime):
        return d.strftime("%d-%m-%Y")
    s = str(d).strip().split(" ")[0]  # quitar parte horaria si viene como "2026-01-01 00:00:00"
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%d-%m-%Y")
        except:
            continue
    return None

def to_datetime_on_date(date_ddmmyyyy, time_hms):
    if not date_ddmmyyyy or not time_hms:
        return None
    try:
        return datetime.strptime(f"{date_ddmmyyyy} {time_hms}", "%d-%m-%Y %H:%M:%S")
    except:
        return None

def minutes_diff(later, earlier):
    if later is None or earlier is None:
        return None
    return (later - earlier).total_seconds() / 60.0

def hhmm_from_minutes(total_min):
    if total_min is None or (isinstance(total_min, float) and np.isnan(total_min)):
        return None
    sign = "-" if total_min < 0 else ""
    m = abs(int(round(total_min)))
    return f"{sign}{m//60:02d}:{m%60:02d}"

def autofit_sheet(worksheet):
    for column in worksheet.columns:
        max_length = 0
        col_letter = column[0].column_letter
        for cell in column:
            try:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            except:
                pass
        worksheet.column_dimensions[col_letter].width = min(max_length + 2, 50)

# ========== ENDPOINTS ==========

@app.get("/")
def read_root():
    return {
        "message": "API Análisis Asistencia Propar - Funcionando",
        "version": "2.0",
        "endpoints": ["/health", "/analizar"]
    }

@app.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.post("/analizar")
async def analizar_asistencia(file: UploadFile = File(...)):
    """
    Analiza un archivo Excel del lector biométrico y retorna el Excel procesado.

    Estructura esperada del archivo:
    - Fila 5: Cabecera (Índice, ID de persona, Nombre, Departamento, Posición,
               Género, Fecha:, Semana, Horario, Registro de entrada, Registro de salida, ...)
    - Filas 6+: Datos
    """
    try:
        logger.info(f"Recibiendo archivo: {file.filename}")

        if not (file.filename.lower().endswith(".xlsx") or file.filename.lower().endswith(".xls")):
            raise HTTPException(status_code=400, detail="El archivo debe ser .xlsx o .xls")

        contents = await file.read()
        excel_io = io.BytesIO(contents)

        # ── LEER EXCEL con cabecera en fila 5 (skiprows=4) ──
        raw = pd.read_excel(excel_io, sheet_name=0, skiprows=4, header=0)
        logger.info(f"Excel cargado: {raw.shape[0]} filas, {raw.shape[1]} columnas")
        logger.info(f"Columnas detectadas: {list(raw.columns)}")

        # Normalizar nombres de columna (por si tienen espacios o variaciones)
        raw.columns = [str(c).strip() for c in raw.columns]

        # Columnas del biométrico
        COL_NOMBRE  = "Nombre"
        COL_DEPT    = "Departamento"
        COL_FECHA   = "Fecha:"
        COL_HORARIO = "Horario"
        COL_ENTRADA = "Registro de entrada"
        COL_SALIDA  = "Registro de salida"

        # Verificar que existen las columnas necesarias
        missing = [c for c in [COL_NOMBRE, COL_DEPT, COL_FECHA, COL_HORARIO, COL_ENTRADA, COL_SALIDA]
                   if c not in raw.columns]
        if missing:
            raise HTTPException(status_code=422,
                detail=f"Columnas no encontradas: {missing}. Columnas disponibles: {list(raw.columns)}")

        # ── CONSTRUIR DataFrame normalizado ──
        df = pd.DataFrame()
        df["sucursal"] = raw[COL_DEPT].apply(dept_to_sucursal)
        df["persona"]  = raw[COL_NOMBRE].astype(str).str.strip()
        df["fecha"]    = raw[COL_FECHA].apply(fmt_ddmmyyyy)

        horarios = raw[COL_HORARIO].apply(parse_horario)
        df["hi"] = horarios.apply(lambda x: x[0])
        df["hf"] = horarios.apply(lambda x: x[1])

        df["entrada"] = raw[COL_ENTRADA].apply(coerce_time)
        df["salida"]  = raw[COL_SALIDA].apply(coerce_time)

        # Filtrar solo filas con fecha válida
        dfb = df[df["fecha"].notna()].copy()
        logger.info(f"Registros válidos: {len(dfb)}")

        # ── ANÁLISIS ──
        records_tarde   = []
        records_missing = []
        records_impos   = []

        for _, r in dfb.iterrows():
            suc  = r["sucursal"]
            per  = r["persona"]
            f    = r["fecha"]
            hi   = r["hi"]
            ent  = r["entrada"]
            sal  = r["salida"]

            # Faltantes
            falta_ent = pd.isna(ent) or ent is None
            falta_sal = pd.isna(sal) or sal is None
            if falta_ent or falta_sal:
                records_missing.append({
                    "sucursal": suc, "persona": per, "fecha": f,
                    "faltante": ("Entrada y salida" if (falta_ent and falta_sal)
                                 else ("Entrada" if falta_ent else "Salida"))
                })

            # Atrasos
            if f and hi and ent:
                dt_hi  = to_datetime_on_date(f, hi)
                dt_ent = to_datetime_on_date(f, ent)
                if dt_hi and dt_ent:
                    tard = minutes_diff(dt_ent, dt_hi)
                    if tard is not None and tard > 0.5:
                        records_tarde.append({
                            "sucursal": suc, "persona": per, "fecha": f,
                            "horario_inicio": hi, "entrada": ent,
                            "atraso_min": round(tard, 2),
                            "atraso_hhmm": hhmm_from_minutes(tard)
                        })

            # Imposibles
            if f and ent and sal:
                dt_ent = to_datetime_on_date(f, ent)
                dt_sal = to_datetime_on_date(f, sal)
                if dt_ent and dt_sal and dt_sal < dt_ent:
                    records_impos.append({
                        "sucursal": suc, "persona": per, "fecha": f,
                        "entrada": ent, "salida": sal,
                        "problema": "Salida anterior a entrada"
                    })

        # ── DATAFRAMES DE ANÁLISIS ──
        df_tarde   = pd.DataFrame(records_tarde)
        df_missing = pd.DataFrame(records_missing)
        df_impos   = pd.DataFrame(records_impos)

        if not df_tarde.empty:
            df_tarde = df_tarde.sort_values(["sucursal", "persona", "fecha"])
            tot_person_suc = (df_tarde.groupby(["sucursal", "persona"], as_index=False)["atraso_min"]
                              .sum())
            tot_person_suc["total_hhmm"] = tot_person_suc["atraso_min"].apply(hhmm_from_minutes)
        else:
            tot_person_suc = pd.DataFrame(columns=["sucursal", "persona", "atraso_min", "total_hhmm"])

        if not df_missing.empty:
            df_missing = df_missing.sort_values(["sucursal", "persona", "fecha"])
            faltantes_tot = (df_missing.groupby(["sucursal", "persona", "faltante"], as_index=False)
                             .size().rename(columns={"size": "cuentas"}))
        else:
            faltantes_tot = pd.DataFrame(columns=["sucursal", "persona", "faltante", "cuentas"])

        if not df_impos.empty:
            df_impos = df_impos.sort_values(["sucursal", "persona", "fecha"])

        # Nunca marcaron
        recs_never = []
        for (suc, per), grp in dfb.groupby(["sucursal", "persona"], dropna=False):
            grp2 = grp[grp["fecha"].notna()]
            if grp2.empty:
                continue
            all_ent_miss = grp2["entrada"].isna().all()
            all_sal_miss = grp2["salida"].isna().all()
            if all_ent_miss or all_sal_miss:
                recs_never.append({
                    "sucursal": suc, "persona": per, "n_dias": len(grp2),
                    "nunca_entrada": "Sí" if all_ent_miss else "No",
                    "nunca_salida":  "Sí" if all_sal_miss else "No"
                })
        df_never = (pd.DataFrame(recs_never).sort_values(["sucursal", "persona"])
                    if recs_never else pd.DataFrame(columns=["sucursal","persona","n_dias","nunca_entrada","nunca_salida"]))

        # KPI puntualidad por sucursal
        def punctual_flag(row):
            if pd.isna(row["fecha"]) or pd.isna(row["hi"]) or pd.isna(row["entrada"]):
                return np.nan
            dt_hi  = to_datetime_on_date(row["fecha"], row["hi"])
            dt_ent = to_datetime_on_date(row["fecha"], row["entrada"])
            if not dt_hi or not dt_ent:
                return np.nan
            return 1 if dt_ent <= dt_hi else 0

        df_kpi = dfb.copy()
        df_kpi["puntual"] = df_kpi.apply(punctual_flag, axis=1)
        kpi_sucursal = (df_kpi.groupby("sucursal", dropna=False)["puntual"]
                        .mean().reset_index())
        kpi_sucursal["puntualidad_%"] = (kpi_sucursal["puntual"] * 100).round(2)
        kpi_sucursal = kpi_sucursal.drop(columns=["puntual"])

        ranking_atrasos = (tot_person_suc.sort_values("atraso_min", ascending=False).head(10)
                           if not tot_person_suc.empty else tot_person_suc)

        df_base = dfb[["sucursal", "persona", "fecha", "hi", "hf", "entrada", "salida"]].copy()
        df_base.rename(columns={"hi": "horario_inicio", "hf": "horario_fin"}, inplace=True)

        # ── CREAR EXCEL DE SALIDA ──
        resumen = pd.DataFrame([
            ["Registros (base)",            len(df_base)],
            ["Llegadas tardías (líneas)",   len(df_tarde)],
            ["Faltantes E/S (líneas)",      len(df_missing)],
            ["Registros sospechosos",       len(df_impos)],
            ["Procesado",                   datetime.now().strftime("%d-%m-%Y %H:%M")]
        ], columns=["Métrica", "Valor"])

        output_io = io.BytesIO()
        with pd.ExcelWriter(output_io, engine="openpyxl") as writer:
            sheets = [
                (resumen,        "Resumen"),
                (kpi_sucursal,   "KPI sucursal"),
                (ranking_atrasos,"Top atrasos"),
                (df_tarde,       "Llegadas tardias"),
                (tot_person_suc, "Totales atraso"),
                (df_missing,     "Faltantes ES detalle"),
                (faltantes_tot,  "Faltantes ES totales"),
                (df_never,       "Nunca marcaron"),
                (df_impos,       "Reg sospechosos"),
                (df_base,        "Base normalizada"),
            ]
            for df_out, sheet_name in sheets:
                df_out.to_excel(writer, index=False, sheet_name=sheet_name)

            for sheet_name in writer.sheets:
                autofit_sheet(writer.sheets[sheet_name])

        output_io.seek(0)
        original_name = file.filename.rsplit(".", 1)[0]
        processed_filename = f"{original_name}_PROCESADO.xlsx"
        logger.info(f"Procesamiento exitoso: {processed_filename} | "
                    f"Atrasos:{len(df_tarde)} Faltantes:{len(df_missing)} Imposibles:{len(df_impos)}")

        return StreamingResponse(
            output_io,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={processed_filename}"}
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error procesando archivo: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error procesando archivo: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
