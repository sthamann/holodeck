"""Identical Qwen input contract for fine-tuning and both inference arms."""
import json


CONTRACT = """Erzeuge ein einziges JSON-Objekt als ausführbaren Holodeck-Plan.
Immer ui als Liste von Komponenten. Jede Komponente ist ein JSON-Objekt mit kind, source, fields (Liste) und optional events, wires, tools, filters, sorts, details, title. Wires ordnet Argumentnamen Referenzen zu.
Checkout: kinds stats, list, form. Quellen quote (goods,discount,shipping,gross,tax,net), cart (name,quantity,unit,gross), _ für Formular.
Formular checkout.place: quote_key=context.quote; name,email,address,consent=input. Formular cart.promo: code=input.
Bei Checkout zusätzlich calc mit genau sechs Ausdrücken für d (Rabatt), x (Rabattsteuer), a (Warenwert), c (Brutto), j (Steuer), n (Netto), in dieser Reihenfolge. Eingaben g=Warenwert vor Rabatt, b=Brutto vor Rabatt, t=Steuer vor Rabatt, r=Steuersatz als ganze Prozentzahl, v=Rabattwert (bei percentage Prozent, bei absolute Cent, bei none 0). Cent-Arithmetik als JSON-Ausdruck: Ganzzahl, Registername oder ["add",a,b], ["sub",a,b], ["min",a,b], ["muldiv",a,b,c] (gerundetes a*b/c).
Admin: kind table, Quelle orders (id,name,gross,stage,payment,delivery,address,items,tracking). Werkzeuge search,sort,filter,expand. Event order_delivery.ship bindet order_id an row.id. where enthält verlangte Gleichheitsfilter aus stage,payment,delivery. sort ist [Feld,"asc" oder "desc"], falls gewünscht. Verwende angefragte Felder und Aktionen."""


def compact(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def prompt(task, request, context):
    return CONTRACT + '\n' + compact({'task':task,'request':request,'context':context}) + '\nPlan:\n'


def pricing_prompt(context):
    return (
        'Erzeuge ein JSON-Rechenprogramm unter calc. '
        'Register: g=Warenwert, b=Brutto, t=Steuer vor Rabatt, r=Steuersatz, v=Rabattwert. '
        'v ist bei percentage ein Prozentsatz, bei absolute ein Festbetrag in Cent, bei none null. '
        'Gib sechs Ausdrücke in der Reihenfolge d Rabatt, x Rabattsteuer, a Warenwert, c Brutto, j Steuer, n Netto. '
        'Ein Ausdruck ist eine Ganzzahl, ein Registername oder ["add",a,b], ["sub",a,b], '
        '["min",a,b], ["muldiv",a,b,c] mit Ganzzahlrundung. Rechne mit Registern, keine Zahlenwerte aus dem Warenkorb kopieren.\n'
        'Aktive Rabattart: '+context['mode']+'\nRegisterbeispiel: '+compact(context)+'\n'
    )


CHECKOUT_REQUESTS = (
    'Baue einen Checkout mit Warenkorb, Rabattfeld, Gesamtpreis und Bestellformular.',
    'Erstelle eine Kasse mit Kostenübersicht, Gutscheinfeld und verbindlicher Testbestätigung.',
    'Zeige die Artikelauswahl und die Beträge und lass mich den Kauf abschließen.',
    'Ich brauche die Bestellübersicht, eine Eingabe für den Aktionscode und den Kaufbutton.',
)
ADMIN_REQUESTS = (
    'Zeig bezahlte Aufträge mit offenem Versand. Die teuersten zuerst. Ich will direkt versenden können.',
    'Bau eine Tabelle mit bezahlten, unversendeten Bestellungen. Betrag absteigend. Mit Versandaktion je Zeile.',
    'Zeig alle Bestellungen als sortierbare Tabelle und ermögliche Versand direkt pro Zeile.',
    'Liste alle Bestellungen. Ich möchte suchen, nach Zahlstatus filtern und die Versandaktion nutzen.',
)
