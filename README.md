# pyfirma

pyfirma es un experimento cuyo objetivo es trasladar la aplicación oficial AutoFirma, de una manera muy simplificada, al lenguaje de programación Python. Este proyecto busca ofrecer una alternativa ligera y fácil de estudiar o modificar.

## Instalación

Para ejecutar este programa, necesitas tener Python instalado. Se recomienda seguir estos pasos:

1. Clona este repositorio o descarga el código fuente.
2. Es recomendable crear un entorno virtual:
   ```bash
   python -m venv venv
   source venv/bin/activate  # En Linux/macOS
   venv\Scripts\activate     # En Windows
   ```
3. Instala las dependencias necesarias:
   ```bash
   pip install -r requirements.txt
   ```

## Ejecución

El programa puede funcionar tanto en modo gráfico (GUI) como en línea de comandos (CLI).

### Modo Gráfico

Para iniciar la interfaz gráfica de usuario, simplemente ejecuta el script principal sin argumentos:

```bash
python main.py
```

Esto abrirá la ventana de la aplicación donde podrás seleccionar el archivo PDF, el certificado y realizar la firma de manera visual.

### Línea de Comandos

También es posible utilizar pyfirma desde la terminal para automatizar procesos. Los argumentos disponibles son:

- `-i`, `--input`: Ruta al archivo PDF de entrada.
- `-c`, `--cert`: Ruta al archivo del certificado (.p12 o .pfx).
- `-p`, `--password`: Contraseña del certificado.
- `-o`, `--output`: (Opcional) Ruta donde se guardará el PDF firmado.

### Pruebas

Puedes comprobar que la firma es correcta usando la web https://valide.redsara.es/

**Ejemplo de uso:**

```bash
python main.py -i documento.pdf -c certificado.p12 -p 12345 -o documento_firmado.pdf
```
