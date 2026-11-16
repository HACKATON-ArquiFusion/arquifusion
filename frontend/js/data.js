// Datos de ejemplo. Reemplazar por llamadas a la API (ver docs/HANDOFF.md)
const Z={Z1:"Tajo Norte",Z2:"Tajo Sur",Z3:"Botadero",Z4:"Planta",Z5:"Taller"};
const ZP={Z1:[30,3,40,28],Z4:[3,38,28,28],Z5:[36,38,28,28],Z3:[69,38,28,28],Z2:[30,70,40,28]};
const M=[
{id:"M-01",mod:"Camión 797F",z:"Z1",x:30,y:55,e:"op",r:8,t:82,v:3.1,p:310,c:92,h:6.8,u:88,$:410},
{id:"M-02",mod:"Excavadora 390F",z:"Z1",x:70,y:60,e:"op",r:14,t:85,v:3.4,p:300,c:55,h:7.1,u:91,$:380},
{id:"M-03",mod:"Excavadora 390F",z:"Z1",x:50,y:35,e:"int",r:71,t:104,v:7.9,p:285,c:68,h:5.9,u:79,$:520},
{id:"M-04",mod:"Cargador 988K",z:"Z3",x:35,y:50,e:"op",r:11,t:80,v:2.9,p:295,c:48,h:6.2,u:84,$:300},
{id:"M-05",mod:"Camión 797F",z:"Z3",x:70,y:60,e:"op",r:19,t:88,v:3.8,p:305,c:95,h:7.4,u:90,$:430},
{id:"M-06",mod:"Perforadora DM45",z:"Z4",x:40,y:45,e:"op",r:9,t:76,v:2.6,p:320,c:30,h:2.1,u:31,$:350},
{id:"M-07",mod:"Cargador 988K",z:"Z4",x:70,y:60,e:"op",r:16,t:83,v:3.2,p:298,c:50,h:6.2,u:86,$:310},
{id:"M-08",mod:"Tractor D10",z:"Z5",x:50,y:55,e:"det",r:40,t:70,v:2.2,p:0,c:0,h:0,u:22,$:290},
{id:"M-09",mod:"Camión 797F",z:"Z2",x:25,y:55,e:"det",r:88,t:112,v:8.8,p:210,c:101,h:4.0,u:60,$:560},
{id:"M-10",mod:"Camión 797F",z:"Z2",x:50,y:35,e:"int",r:64,t:98,v:6.1,p:240,c:110,h:6.6,u:82,$:540},
{id:"M-11",mod:"Cargador 988K",z:"Z2",x:75,y:60,e:"int",r:58,t:95,v:5.7,p:250,c:72,h:6.0,u:80,$:500},
{id:"M-12",mod:"Tractor D10",z:"Z5",x:25,y:40,e:"op",r:12,t:79,v:2.8,p:315,c:40,h:1.4,u:18,$:330}];
const T=[{id:"t1",n:"Luis Quispe",es:"Hidráulica",z:"Z5",f:1},{id:"t2",n:"Marta Rojas",es:"Motor",z:"Z1",f:1},{id:"t3",n:"Diego Salas",es:"Eléctrica",z:"Z3",f:0}];
const I=[
{id:"INC-104",m:"M-03",s:"Alta",es:"Motor",c:"Vibración y temperatura suben juntas desde hace 3 días. Posible rodamiento.",t:"t1",st:"asig",h:"08:12"},
{id:"INC-105",m:"M-09",s:"Crítica",es:"Hidráulica",c:"Presión hidráulica cayó. Máquina detenida.",t:null,st:"nuevo",h:"09:40"},
{id:"INC-106",m:"M-10",s:"Media",es:"Motor",c:"Consumo de combustible 18% sobre lo esperado.",t:null,st:"nuevo",h:"10:05"}];
const R=[["Filtro hidráulico",2,9,"bajo","Z2"],["Rodamiento eje",4,5,"justo","Z1"],["Sello de bomba",12,3,"ok","Z5"],["Correa alternador",8,2,"ok","Z3"]];
const U={op:{n:"Carlos Mendoza",r:"Operador",e:"operador@arquifusion.pe",d:"Opera la máquina M-07",m:"M-07"},tec:{n:"Luis Quispe",r:"Técnico",e:"tecnico@arquifusion.pe",d:"Atiende incidentes asignados",t:"t1"},sup:{n:"Ana Torres",r:"Supervisor",e:"supervisor@arquifusion.pe",d:"Coordina la operación"},ger:{n:"Jorge Valdivia",r:"Gerente",e:"gerente@arquifusion.pe",d:"Decide costos y flota"}};
const NAV={op:["Mi máquina"],tec:["Mis incidentes","Repuestos"],sup:["Operación","Preventivo y repuestos"],ger:["Resumen","Productividad y costos"]};
