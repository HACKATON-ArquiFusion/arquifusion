# Simulador ArquiFusion

## Historico
python simulador/simular.py historico --n 50 --dias 90 --semilla 42 --salida simulador/salida --paso-min 60 --tasa-falla 0.5

## Verificacion
python simulador/verificar.py

## Vivo (dry-run por defecto)
python simulador/simular.py vivo --n 50 --intervalo-seg 5 --max-ciclos 5 --aceleracion 1 --inyectar-falla M-014

Para enviar, configura AWS_API_URL y AWS_API_KEY (o un .env local) y agrega --enviar.
El envio usa POST {AWS_API_URL}/ingesta, header x-api-key y lotes de maximo 500 registros.
