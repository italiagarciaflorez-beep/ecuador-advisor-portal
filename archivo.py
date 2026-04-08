import base64, pathlib

# Usa UNA de estas 3 formas de ruta:
# p = r"D:\Users\igarcia\Downloads\iconic-access-469720-d3-cc6c46d5c6a1.json"  # raw string
# p = "D:\\Users\\igarcia\\Downloads\\iconic-access-469720-d3-cc6c46d5c6a1.json" # doble backslash
p = "D:/Users/igarcia/Downloads/iconic-access-469720-d3-cc6c46d5c6a1.json"       # slashes normales

data = pathlib.Path(p).read_bytes()                 # lee en binario
print(base64.b64encode(data).decode("utf-8"))       # imprime el base64

