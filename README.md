# Orar pe grupe

Aplicație desktop Tkinter care primește un fișier PDF cu orarul, extrage tabelul după coordonatele vizuale și afișează activitățile sortate după grupă.

## Pornire

```powershell
pip install -r requirements.txt
python app.py
```

În fereastra aplicației apasă **Deschide PDF** și selectează fișierul.

Pe Windows poți porni aplicația și cu dublu-click pe `porneste_aplicatia.bat`.

Când aceeași celulă conține două discipline la aceeași oră, prima este marcată
**Impară**, iar a doua **Pară**. Filtrul „Săptămână” permite afișarea uneia
dintre ele. În această aplicație, săptămâna impară este săptămâna care începe
la 1 septembrie.

## Format PDF recomandat

PDF-ul trebuie să aibă text selectabil, nu doar imagini scanate. Pentru formatul furnizat sunt citite direct coloanele grupelor, zilele și intervalele din tabel.

- zile în limba română;
- intervale de forma `08:00-10:00`;
- grupe de forma `TI-221`, `A1`, `grupa 203`;
- săli de forma `sala A101`, `Room 203` sau `amf. 1`.

Dacă formatul PDF-ului este tabelar și textul nu este extras corect, aplicația afișează textul extras în secțiunea de diagnostic pentru ajustarea regulilor.
