from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import re
import io
import logging

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="API Análisis Asistencia Propar")

# CORS - permitir requests desde n8n
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== FUNCIONES HELPER ==========

KNOWN_BRANCHES = ["Matriz", "Oviedo", "Ciudad del Este (CDE)", "Encarnación", "Hohenau"]
BRANCH_ALIASES = {
    "MATRIZ": "Matriz",
    "OVIEDO": "Oviedo",
    "CORONEL OVIEDO": "Oviedo",
    "CDE": "Ciudad del Este (CDE)",
    "CIUDAD DEL ESTE": "Ciudad del Este (CDE)",
    "ENCARN": "Encarnación",
    "ENCARNACION": "Encarnación",
    "ENCARNACIÓN": "Encarnación",
    "HOHE": "Hohenau",
    "HOHENAU": "Hohenau",
}

def hhmm_from_minutes(total_min):
    if total_min is None or (isinstance(total_min, float) and np.isnan(total_min)):
        return None
    sign = "-" if total_min < 0 else ""
    m = abs(int(round(total_min)))
    return f"{sign}{m//60:02d}:{m%60:02d}"

def coerce_time(s: str):
    if pd.isna(s):
        return None
    t = str(s).strip()
    if t in ["", "-", "—", "–", "null", "None"]:
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", t)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))
    ss = int(m.group(3) or 0)
    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
        return None
    return f"{hh:02d}:{mm:02d}:{ss:02d}"

def parse_range(s: str):
    if pd.isna(s):
        return (None, None)
    txt = str(s).strip()
    m = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?)\s*[-–]\s*(\d{1,2}:\d{2}(?::\d{2})?)", txt)
    if not m:
        return (None, None)
    a = coerce_time(m.group(1))
    b = coerce_time(m.group(2))
    return a, b

def fmt_ddmmyyyy(d):
    if isinstance(d, datetime):
        return d.strftime("%d-%m-%Y")
    if isinstance(d, (int, float)) and not pd.isna(d):
        base = datetime(1899, 12, 30)
        try:
            dt = base + timedelta(days=float(d))
            return dt.strftime("%d-%m-%Y")
        except:
            return None
    s = str(d).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%d-%m-%Y")
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

def detect_branch_row(row_vals):
    for v in row_vals:
        if pd.isna(v):
            continue
        s = str(v).upper()
        for kb in KNOWN_BRANCHES:
            if kb.upper() in s:
                return kb
        for alias, norm in BRANCH_ALIASES.items():
            if alias in s:
                return norm
    return np.nan

def safe_col(df, idx1):
    idx0 = idx1 - 1
    return df.iloc[:, idx0] if df.shape[1] > idx0 else pd.Series([np.nan] * len(df))

# ========== ENDPOINTS ==========

@app.get("/")
def read_root():
    return {
        "message": "API Análisis Asistencia Propar - Funcionando",
        "version": "1.0",
        "endpoints": ["/health", "/analizar"]
    }

@app.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.post("/analizar")
async def analizar_asistencia(file: UploadFile = File(...)):
    """
    Analiza un archivo Excel de asistencia y retorna el Excel procesado.
    """
    try:
        logger.info(f"Recibiendo archivo: {file.filename}")
        
        # Validar extensión
        if not (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
            raise HTTPException(status_code=400, detail="El archivo debe ser .xlsx o .xls")
        
        # Leer el archivo
        contents = await file.read()
        excel_io = io.BytesIO(contents)
        
        # Procesar Excel
        raw = pd.read_excel(excel_io, sheet_name=0, header=None)
        logger.info(f"Excel cargado: {raw.shape[0]} filas, {raw.shape[1]} columnas")
        
        # Extraer columnas
        col_nombre = safe_col(raw, 3)
        col_fecha = safe_col(raw, 7)
        col_rango = safe_col(raw, 9)
        col_ent = safe_col(raw, 10)
        col_sal = safe_col(raw, 11)
        
        # Detectar sucursales
        suc_list = [detect_branch_row(row.values) for _, row in raw.iterrows()]
        sucursal = pd.Series(suc_list)
        
        # Crear DataFrame normalizado
        df = pd.DataFrame({
            "sucursal": sucursal,
            "persona_raw": col_nombre,
            "fecha_raw": col_fecha,
            "rango_raw": col_rango,
            "entrada_raw": col_ent,
            "salida_raw": col_sal
        })
        
        # Normalizar datos
        df["persona"] = df["persona_raw"].astype(str).str.strip()
        df["fecha"] = df["fecha_raw"].apply(fmt_ddmmyyyy)
        df[["hi", "hf"]] = df["rango_raw"].apply(lambda x: pd.Series(parse_range(x)))
        df["entrada"] = df["entrada_raw"].apply(coerce_time)
        df["salida"] = df["salida_raw"].apply(coerce_time)
        
        # Filtrar registros válidos
        mask_valid = df["fecha"].notna() & (df["entrada"].notna() | df["salida"].notna() | df["hi"].notna())
        dfb = df.loc[mask_valid].copy()
        
        logger.info(f"Registros válidos: {len(dfb)}")
        
        # Analizar llegadas tardías, faltantes, imposibles
        records_tarde = []
        records_missing = []
        records_impos = []
        
        for _, r in dfb.iterrows():
            suc = r.get("sucursal", np.nan)
            per = r.get("persona", np.nan)
            f = r.get("fecha", None)
            hi = r.get("hi", None)
            ent = r.get("entrada", None)
            sal = r.get("salida", None)
            
            # Detectar faltantes
            falta_ent = ent is None
            falta_sal = sal is None
            if (f is not None) and (falta_ent or falta_sal):
                records_missing.append({
                    "sucursal": suc,
                    "persona": per,
                    "fecha": f,
                    "faltante": "Entrada y salida" if (falta_ent and falta_sal) else ("Entrada" if falta_ent else "Salida")
                })
            
            # Detectar atrasos
            if f and hi and ent:
                dt_hi = to_datetime_on_date(f, hi)
                dt_ent = to_datetime_on_date(f, ent)
                if dt_hi and dt_ent:
                    tard = minutes_diff(dt_ent, dt_hi)
                    if (tard is not None) and (tard > 0.5):
                        records_tarde.append({
                            "sucursal": suc,
                            "persona": per,
                            "fecha": f,
                            "horario_inicio": hi,
                            "entrada": ent,
                            "atraso_min": round(tard, 2),
                            "atraso_hhmm": hhmm_from_minutes(tard)
                        })
            
            # Detectar registros imposibles
            if f and ent and sal:
                dt_ent = to_datetime_on_date(f, ent)
                dt_sal = to_datetime_on_date(f, sal)
                if dt_ent and dt_sal and dt_sal < dt_ent:
                    records_impos.append({
                        "sucursal": suc,
                        "persona": per,
                        "fecha": f,
                        "entrada": ent,
                        "salida": sal,
                        "problema": "Salida anterior a entrada"
                    })
        
        # Crear DataFrames de análisis
        df_tarde = pd.DataFrame(records_tarde)
        df_missing = pd.DataFrame(records_missing)
        df_impos = pd.DataFrame(records_impos)
        
        if not df_tarde.empty:
            df_tarde = df_tarde.sort_values(["sucursal", "persona", "fecha"])
            tot_person_suc = df_tarde.groupby(["sucursal", "persona"], as_index=False)["atraso_min"].sum()
            tot_person_suc["total_hhmm"] = tot_person_suc["atraso_min"].apply(hhmm_from_minutes)
        else:
            tot_person_suc = pd.DataFrame(columns=["sucursal", "persona", "atraso_min", "total_hhmm"])
        
        if not df_missing.empty:
            df_missing = df_missing.sort_values(["sucursal", "persona", "fecha"])
            faltantes_tot = df_missing.groupby(["sucursal", "persona", "faltante"], as_index=False).size()
            faltantes_tot = faltantes_tot.rename(columns={"size": "cuentas"})
        else:
            faltantes_tot = pd.DataFrame(columns=["sucursal", "persona", "faltante", "cuentas"])
        
        if not df_impos.empty:
            df_impos = df_impos.sort_values(["sucursal", "persona", "fecha"])
        
        # Nunca marcaron
        df_never = pd.DataFrame(columns=["sucursal", "persona", "n_dias", "nunca_entrada", "nunca_salida"])
        if not dfb.empty:
            recs = []
            for (suc, per), grp in dfb.groupby(["sucursal", "persona"], dropna=False):
                grp2 = grp[grp["fecha"].notna()]
                if grp2.empty:
                    continue
                all_ent_missing = grp2["entrada"].isna().all()
                all_sal_missing = grp2["salida"].isna().all()
                if all_ent_missing or all_sal_missing:
                    recs.append({
                        "sucursal": suc,
                        "persona": per,
                        "n_dias": len(grp2),
                        "nunca_entrada": "Sí" if all_ent_missing else "No",
                        "nunca_salida": "Sí" if all_sal_missing else "No"
                    })
            if recs:
                df_never = pd.DataFrame(recs).sort_values(["sucursal", "persona"])
        
        # KPI por sucursal
        def punctual_flag(row):
            f, hi, ent = row["fecha"], row["hi"], row["entrada"]
            if pd.isna(f) or pd.isna(hi) or pd.isna(ent):
                return np.nan
            dt_hi = to_datetime_on_date(f, hi)
            dt_ent = to_datetime_on_date(f, ent)
            if dt_hi is None or dt_ent is None:
                return np.nan
            return 1 if dt_ent <= dt_hi else 0
        
        df_kpi = dfb.copy()
        df_kpi["puntual"] = df_kpi.apply(punctual_flag, axis=1)
        kpi_sucursal = df_kpi.groupby("sucursal", dropna=False)["puntual"].mean().reset_index()
        kpi_sucursal["puntualidad_%"] = (kpi_sucursal["puntual"] * 100).round(2)
        kpi_sucursal = kpi_sucursal.drop(columns=["puntual"])
        
        # Ranking atrasos
        ranking_atrasos = tot_person_suc.sort_values("atraso_min", ascending=False).head(10) if not tot_person_suc.empty else tot_person_suc
        
        # Base normalizada
        df_base = dfb[["sucursal", "persona", "fecha", "hi", "hf", "entrada", "salida"]].copy()
        df_base.rename(columns={"hi": "horario_inicio", "hf": "horario_fin"}, inplace=True)
        
        # Crear Excel de salida
        output_io = io.BytesIO()
        with pd.ExcelWriter(output_io, engine="openpyxl") as writer:
            # Resumen
            resumen = pd.DataFrame([
                ["Registros (base)", len(df_base)],
                ["Llegadas tardías (líneas)", len(df_tarde)],
                ["Faltantes E/S (líneas)", len(df_missing)],
                ["Registros sospechosos", len(df_impos)],
                ["Procesado", datetime.now().strftime("%d-%m-%Y %H:%M")]
            ], columns=["Métrica", "Valor"])
            resumen.to_excel(writer, index=False, sheet_name="Resumen")
            
            # Otras hojas
            kpi_sucursal.to_excel(writer, index=False, sheet_name="KPI sucursal")
            ranking_atrasos.to_excel(writer, index=False, sheet_name="Top atrasos")
            df_tarde.to_excel(writer, index=False, sheet_name="Llegadas tardias")
            tot_person_suc.to_excel(writer, index=False, sheet_name="Totales atraso")
            df_missing.to_excel(writer, index=False, sheet_name="Faltantes ES detalle")
            faltantes_tot.to_excel(writer, index=False, sheet_name="Faltantes ES totales")
            df_never.to_excel(writer, index=False, sheet_name="Nunca marcaron")
            df_impos.to_excel(writer, index=False, sheet_name="Reg sospechosos")
            df_base.to_excel(writer, index=False, sheet_name="Base normalizada")
            
            # AUTO-AJUSTAR ANCHO DE COLUMNAS
            for sheet_name in writer.sheets:
                worksheet = writer.sheets[sheet_name]
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    
                    for cell in column:
                        try:
                            if cell.value:
                                cell_length = len(str(cell.value))
                                if cell_length > max_length:
                                    max_length = cell_length
                        except:
                            pass
                    
                    # Ajustar ancho con un poco de padding
                    adjusted_width = min(max_length + 2, 50)  # Máximo 50 caracteres
                    worksheet.column_dimensions[column_letter].width = adjusted_width
        
        output_io.seek(0)
        
        # Generar nombre del archivo procesado
        original_name = file.filename.replace('.xlsx', '').replace('.xls', '')
        processed_filename = f"{original_name}_PROCESADO.xlsx"
        
        logger.info(f"Procesamiento exitoso: {processed_filename}")
        
        return StreamingResponse(
            output_io,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={processed_filename}"}
        )
        
    except Exception as e:
        logger.error(f"Error procesando archivo: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error procesando archivo: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
