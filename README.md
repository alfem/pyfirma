# pyfirma

pyfirma es un experimento cuyo objetivo es trasladar la aplicación oficial AutoFirma (de una manera muy simplificada) al lenguaje de programación Python. Este proyecto busca ofrecer una alternativa a AutoFirma ligera y más fácil de estudiar o modificar.

## Instalación

Lee la guía detallada de [instalación](instalacion.md) para descargar e instalar Pyfirma en un ordenador con Linux (probado en Ubuntu). 

## Ejecución

El programa puede funcionar tanto en modo gráfico (GUI) como en línea de comandos (CLI). Se puede usar de forma independiente, para firmar ficheros locales, o asociado al navegador web (para firmas online).

### Modo Gráfico

Para iniciar la interfaz gráfica de usuario, simplemente ejecuta el script principal sin argumentos:

```bash
python main.py
```

Esto abrirá la ventana de la aplicación donde podrás seleccionar el archivo PDF, el certificado y realizar la firma de manera visual.

Puede seleccionar la casilla **"Add Visible Signature"** para añadir un sello visible en la primera página del documento con el nombre del firmante y la fecha.

### Línea de Comandos

También es posible utilizar pyfirma desde la terminal para automatizar procesos. Los argumentos disponibles son:

- `-i`, `--input`: Ruta al archivo PDF de entrada.
- `-c`, `--cert`: Ruta al archivo del certificado (.p12 o .pfx).
- `-p`, `--password`: Contraseña del certificado.
- `-o`, `--output`: (Opcional) Ruta donde se guardará el PDF firmado.
- `--visible`: (Opcional) Añade un sello visible con el nombre y fecha en la primera página.
- `--vertical-left`: (Opcional) Coloca el sello visible en el margen izquierdo, centrado verticalmente y girado 90 grados.


**Ejemplo de uso:**

```bash
python main.py -i documento.pdf -c certificado.p12 -p 12345 -o documento_firmado.pdf --visible
```
### Pruebas

Puedes comprobar que una firma es correcta usando la web https://valide.redsara.es/

**Más información**

Si quieres modificar y adaptar este programa, tienes [información adicional sobre el protocolo afirma](procolo.md).
