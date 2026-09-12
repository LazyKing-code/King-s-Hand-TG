"""Clean 4- and 5-letter word banks for group Wordle.

Kept family-friendly on purpose — no insults, slurs, or crude slang.
"""
from __future__ import annotations

WORDS_4: frozenset[str] = frozenset(
    w.upper()
    for w in """
    able acid aged also area army away baby back ball band bank base bath bear beat
    been belt best bike bill bird blow blue boat body book born both bowl bush
    busy cake call calm came camp card care case cash cast cave chat chip city clap
    clay club coal coat code cold come cook cool copy corn cost crew crop dark data
    date dawn days deal dear deep desk dial diet dirt dish door down draw drop
    drum duck dust each earn east easy edge else even ever face fact fail fair fall
    farm fast fear feed feel file fill film find fine fire firm fish five flag flat
    flow foam fold food foot ford form fort four free from fuel full fund gain game
    gate gave gear gift girl give glad glow goal goat gold golf gone good grab gray
    grew grin grow hair half hall hand hard have head hear heat held help
    here hero hide high hill hire hold hole home hope host hour huge hung hunt idea
    inch into iron item join joke jump just keep kept kick kind king knee knew know
    lace lake land lane last late lead leaf left less life lift like line link list
    live load loan lock long look lord lose love luck made mail main make
    male mall many mark mask mass meal mean meat meet menu mile milk mind mine miss
    mode mood moon more most move much must name near neck need news next nice nine
    none nose note okay once only onto open over pace pack page paid pain pair palm
    park part pass path peak pick pile pine pink plan play plot plus poem pole pool
    poor port post pull pure push race rain rank rare rate read real rely rest rice
    rich ride ring rise risk road rock role roll roof room root rose rule safe sail
    sale salt same sand save seat seed seek seem self sell send ship shop shot show
    shut side sign silk sing site size skin slip slow snow soft soil sold some song
    soon sort soul soup spin star stay step stop such suit sure swim take talk tall
    team tell term test text than that them then they thin this thus tide tied time
    tiny tone took tool tour town tree trip true tune turn twin type unit upon used
    user vary vast very view vote wait wake walk wall want warm warn wash wave weak
    wear week well went were west what when wide wife wild will wind wine wing wire
    wise wish with wood word wore work yard year your zone
    """.split()
)

WORDS_5: frozenset[str] = frozenset(
    w.upper()
    for w in """
    about above actor acute adapt admit adopt adult after again agent agree
    ahead alarm album alert alien align alike alive allow alone along alter among
    angle apart apple apply arena argue arise array aside asset audio
    audit avoid award aware badly baker bases basic basis beach began begin being
    below bench billy birth black blank blast blind block board boost
    booth bound brain brand bread break breed brief bring broad broke brown build
    built buyer cable candy canal carry catch cause chain chair chalk chaos
    charm chart chase cheap check chest chief child china chose civil claim class
    clean clear click clock close cloud coach coast could count court cover craft
    cream cross crowd crown dance dealt debut delay depth diary
    dirty doing dozen draft drama drawn dream dress drill drink drive drove
    early earth eight elite empty enjoy enter entry equal error event
    every exact exist extra faith false fibre field fifth fifty fight final
    first fixed flash fleet floor focus force forth forty forum found frame frank
    fresh front fruit fully funny giant given glass globe going grace grade
    grand grant grass great green group grown guard guess guest guide happy harry
    heart heavy hence henry horse hotel house human ideal image index inner input
    issue joint jones judge known label large laser later laugh layer learn least
    leave legal level light limit links lives local logic loose lower lucky lunch
    lying magic major maker march match maybe mayor media metal might minor minus
    mixed model money month moral motor mount mouse mouth movie music needs never
    newly night noise north novel nurse occur ocean offer often order other ought
    paint panel paper party peace peter phase phone photo piece pilot pitch place
    plain plane plant plate point pound power press price pride prime print prior
    prize proof proud prove queen quick quiet quite radio raise range rapid ratio
    reach ready refer right river roger roman rough round route royal rural scale
    scene scope score sense serve seven shall shape share sharp sheet shelf shell
    shift shine shirt shock shoot short shown sides sight since sixth sixty skill
    sleep slide small smart smile smith smoke solid solve sorry sound south space
    spare spend split spoke sport staff stage stake stand start state steam steel
    steep stick still stock stone stood store storm story strip stuck study stuff
    style sugar suite super sweet table taken taste taxes teach teeth thank theme
    there these thick thing think third those three threw throw tight times tired
    title today topic total touch tower track trade train treat trend trial tried
    tries truck truly trust truth twice under union unity until upper upset urban
    usage usual valid value video visit vital voice watch water wheel
    where which while white whole whose woman world worth would
    write wrote young youth
    """.split()
)


def words_for(length: int) -> frozenset[str]:
    if length == 4:
        return frozenset(w for w in WORDS_4 if len(w) == 4)
    if length == 5:
        return frozenset(w for w in WORDS_5 if len(w) == 5)
    raise ValueError("length must be 4 or 5")
