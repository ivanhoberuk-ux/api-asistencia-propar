# Configuración n8n + Azure (Document Intelligence)

## 1) Variables de entorno n8n
Definir en el entorno de ejecución de n8n:

- `AZURE_DI_ENDPOINT=https://ocr-lotes.cognitiveservices.azure.com/`
- `AZURE_DI_KEY=<tu_api_key_document_intelligence>`

> No hardcodear estas variables dentro del workflow.

## 2) Credenciales Azure Blob en n8n
Crear credencial **Azure Storage** para los nodos:

- **Account Name**: `lotesocrstorage`
- **Authentication**:
  - Opción A: `Account Key`
  - Opción B: `Connection String`

Luego asociar la credencial a:
- `Trigger Blob Creado`
- `Descargar PDF Blob`
- `Subir XLSX a out/`

## 3) Parámetros clave del workflow
- **Container**: `pericias`
- **Prefix trigger**: `in/`
- **Salida**: `out/{archivo_original}.xlsx`
- **Hoja XLSX**: `Lotes`

## 4) Validación de salida
El parser genera y valida el orden exacto de columnas:
1. Manzana
2. Lote
3. Norte (medida en metros)
4. Linda Norte (quién linda al norte)
5. Sur (medida en metros)
6. Linda Sur (quién linda al sur)
7. Este (medida en metros)
8. Linda Este (quién linda al este)
9. Oeste (medida en metros)
10. Linda Oeste (quién linda al oeste)
11. Superficie m2
12. Comentarios

Si faltan datos, deja celda vacía y agrega motivo en `Comentarios`.
