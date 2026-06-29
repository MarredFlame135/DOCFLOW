import pdfplumber
import re
import os

def limpiar_nombre_archivo(nombre_archivo):
    s = re.sub(r'\.pdf$', '', nombre_archivo, flags=re.IGNORECASE)
    s = s.replace('-ICAT PUEBLA', '').replace('ICAT PUEBLA', '')
    s = re.sub(r'^[\d\s\.\-_]+', '', s) # Quita números y basura inicial
    return s.strip().upper()

def extraer_datos_certificado(ruta_pdf):
    nombre_archivo = os.path.basename(ruta_pdf)
    nombre_limpio = limpiar_nombre_archivo(nombre_archivo)
    
    texto = ""
    # El uso de 'with' es obligatorio aquí para liberar la RAM en cada ciclo
    try:
        with pdfplumber.open(ruta_pdf) as pdf:
            for pagina in pdf.pages:
                t = pagina.extract_text()
                if t: texto += t + "\n"
                pagina.flush_cache() # Limpia la memoria de la página procesada
    except:
        pass

    curso = "N/A"
    fecha = "N/A"
    
    if texto:
        # Búsqueda optimizada (regex rápida)
        curso_m = re.search(r"Estándar de\s+Competencia\s*\n+(.*?)\s*\n*Inscrito", texto, re.IGNORECASE | re.DOTALL)
        if curso_m: curso = curso_m.group(1).strip().replace("\n", " ")
        
        fecha_m = re.search(r",\s+a\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})", texto, re.IGNORECASE)
        if fecha_m: fecha = fecha_m.group(1)

    return {
        "Nombre": nombre_limpio,
        "Curso": curso,
        "Fecha": fecha
    }