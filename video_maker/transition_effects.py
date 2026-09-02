from dataclasses import dataclass

import wx

from video_maker.app_state import get_language
from video_maker.dialog_keys import bind_dialog_keys
from video_maker.localization import tr


TRANSITION_DURATIONS = [
    ("0.5", 0.5),
    ("1", 1.0),
    ("1.5", 1.5),
    ("2", 2.0),
    ("3", 3.0),
]


@dataclass(frozen=True)
class TransitionEffectSelection:
    key: str
    name: str
    description: str
    duration: float


GROUPS = [
    {
        "name": {"ar": "التلاشي والتحول الهادئ", "en": "Fade and gentle change", "fr": "Fondus et transitions douces"},
        "effects": [
            ("fade", {"ar": "تلاشي متبادل", "en": "Crossfade", "fr": "Fondu croise"}, {
                "ar": "تختفي الصورة الحالية تدريجيا في الوقت نفسه الذي تظهر فيه الصورة التالية. تتداخل الصورتان بشفافية حتى تختفي الأولى وتبقى الثانية كاملة.",
                "en": "The current picture fades out while the next picture fades in. The two pictures overlap softly until the next one remains clear.",
                "fr": "L'image actuelle disparait pendant que l'image suivante apparait. Les deux images se mélangent doucement jusqu'a ce que la suivante reste seule.",
            }),
            ("fadeblack", {"ar": "تلاشي عبر الأسود", "en": "Fade through black", "fr": "Fondu par le noir"}, {
                "ar": "تظلم الصورة الحالية تدريجيا حتى تصبح الشاشة سوداء، ثم تظهر الصورة التالية تدريجيا من اللون الأسود حتى تصبح واضحة بالكامل.",
                "en": "The current picture darkens to black, then the next picture appears from black until it is fully clear.",
                "fr": "L'image actuelle s'assombrit jusqu'au noir, puis l'image suivante apparait depuis le noir jusqu'a devenir nette.",
            }),
            ("fadegrays", {"ar": "تلاشي عبر الرمادي", "en": "Fade through gray", "fr": "Fondu par le gris"}, {
                "ar": "تفقد الصورة الحالية ألوانها تدريجيا وتتحول إلى درجات رمادية، ثم تحل الصورة التالية مكانها وتعود ألوانها تدريجيا حتى تظهر كاملة.",
                "en": "The current picture loses color into gray tones, then the next picture replaces it and regains full color.",
                "fr": "L'image actuelle perd ses couleurs vers des tons gris, puis l'image suivante la remplace et retrouve ses couleurs.",
            }),
            ("dissolve", {"ar": "ذوبان نقطي", "en": "Dissolve", "fr": "Dissolution"}, {
                "ar": "تبدأ نقاط صغيرة موزعة في أنحاء الصورة الحالية بالتحول إلى أجزاء من الصورة التالية. يزداد عدد النقاط الجديدة تدريجيا حتى تختفي الصورة الحالية تماما.",
                "en": "Small points across the current picture turn into parts of the next picture until the change is complete.",
                "fr": "De petits points de l'image actuelle deviennent des parties de l'image suivante jusqu'au remplacement complet.",
            }),
            ("hblur", {"ar": "تمويه أفقي", "en": "Horizontal blur", "fr": "Flou horizontal"}, {
                "ar": "تبدأ تفاصيل الصورة الحالية بالتمدد والتمويه أفقيا، ثم تظهر الصورة التالية من خلال هذا التمويه وتزداد وضوحا حتى تصبح حادة وكاملة.",
                "en": "The current picture blurs horizontally, then the next picture appears through the blur and becomes sharp.",
                "fr": "L'image actuelle devient floue horizontalement, puis l'image suivante apparait dans ce flou et devient nette.",
            }),
            ("fadeslow", {"ar": "تلاشي هادئ ممتد", "en": "Slow gentle fade", "fr": "Fondu lent et doux"}, {
                "ar": "تتحول الصورة الحالية إلى الصورة التالية بتلاشي هادئ موزع على مدة الانتقال كاملة، من غير حركة أو حافة فاصلة ظاهرة.",
                "en": "The current picture changes into the next one with a slow fade over the full transition time, without a moving edge.",
                "fr": "L'image actuelle passe a la suivante par un fondu lent sur toute la duree, sans bord mobile visible.",
            }),
        ],
    },
    {
        "name": {"ar": "الانتقال الناعم في اتجاه واحد", "en": "Smooth one-way movement", "fr": "Mouvement doux dans une direction"},
        "effects": [
            ("smoothleft", {"ar": "انتقال ناعم نحو اليسار", "en": "Smooth left", "fr": "Transition douce vers la gauche"}, {"ar": "يمر حد ناعم وممزوج عبر الشاشة نحو اليسار، ويستبدل الصورة الحالية بالصورة التالية تدريجيا من غير حافة حادة.", "en": "A soft blended edge moves left and gradually replaces the current picture with the next one.", "fr": "Un bord doux se deplace vers la gauche et remplace progressivement l'image actuelle."}),
            ("smoothright", {"ar": "انتقال ناعم نحو اليمين", "en": "Smooth right", "fr": "Transition douce vers la droite"}, {"ar": "يمر حد ناعم وممزوج عبر الشاشة نحو اليمين، ويستبدل الصورة الحالية بالصورة التالية تدريجيا من غير حافة حادة.", "en": "A soft blended edge moves right and gradually replaces the current picture with the next one.", "fr": "Un bord doux se deplace vers la droite et remplace progressivement l'image actuelle."}),
            ("smoothup", {"ar": "انتقال ناعم نحو الأعلى", "en": "Smooth up", "fr": "Transition douce vers le haut"}, {"ar": "يمر حد أفقي ناعم وممزوج نحو أعلى الشاشة، ويستبدل الصورة الحالية بالصورة التالية تدريجيا.", "en": "A soft horizontal edge moves upward and gradually replaces the current picture.", "fr": "Un bord horizontal doux monte et remplace progressivement l'image actuelle."}),
            ("smoothdown", {"ar": "انتقال ناعم نحو الأسفل", "en": "Smooth down", "fr": "Transition douce vers le bas"}, {"ar": "يمر حد أفقي ناعم وممزوج نحو أسفل الشاشة، ويستبدل الصورة الحالية بالصورة التالية تدريجيا.", "en": "A soft horizontal edge moves downward and gradually replaces the current picture.", "fr": "Un bord horizontal doux descend et remplace progressivement l'image actuelle."}),
        ],
    },
    {
        "name": {"ar": "المسح المباشر", "en": "Direct wipe", "fr": "Balayage direct"},
        "effects": [
            ("wipeleft", {"ar": "مسح نحو اليسار", "en": "Wipe left", "fr": "Balayage vers la gauche"}, {"ar": "يتحرك خط فاصل مستقيم نحو اليسار. تختفي الصورة الحالية خلف الخط، وتظهر الصورة التالية في المساحة التي مر عليها.", "en": "A straight edge moves left. The current picture disappears behind it and the next picture appears.", "fr": "Une ligne droite se deplace vers la gauche. L'image actuelle disparait derriere elle et la suivante apparait."}),
            ("wiperight", {"ar": "مسح نحو اليمين", "en": "Wipe right", "fr": "Balayage vers la droite"}, {"ar": "يتحرك خط فاصل مستقيم نحو اليمين. تختفي الصورة الحالية خلف الخط، وتظهر الصورة التالية في المساحة التي مر عليها.", "en": "A straight edge moves right. The current picture disappears behind it and the next picture appears.", "fr": "Une ligne droite se deplace vers la droite. L'image actuelle disparait derriere elle et la suivante apparait."}),
            ("wipeup", {"ar": "مسح نحو الأعلى", "en": "Wipe up", "fr": "Balayage vers le haut"}, {"ar": "يتحرك خط أفقي مستقيم نحو أعلى الشاشة. تختفي الصورة الحالية خلفه، وتظهر الصورة التالية تدريجيا.", "en": "A straight horizontal edge moves upward. The current picture disappears and the next picture appears.", "fr": "Une ligne horizontale monte. L'image actuelle disparait et la suivante apparait."}),
            ("wipedown", {"ar": "مسح نحو الأسفل", "en": "Wipe down", "fr": "Balayage vers le bas"}, {"ar": "يتحرك خط أفقي مستقيم نحو أسفل الشاشة. تختفي الصورة الحالية خلفه، وتظهر الصورة التالية تدريجيا.", "en": "A straight horizontal edge moves downward. The current picture disappears and the next picture appears.", "fr": "Une ligne horizontale descend. L'image actuelle disparait et la suivante apparait."}),
        ],
    },
    {
        "name": {"ar": "انزلاق الصورتين", "en": "Sliding pictures", "fr": "Glissement des images"},
        "effects": [
            ("slideleft", {"ar": "انزلاق نحو اليسار", "en": "Slide left", "fr": "Glissement vers la gauche"}, {"ar": "تتحرك الصورة الحالية كاملة نحو خارج الشاشة من اليسار، وفي الوقت نفسه تدخل الصورة التالية من اليمين حتى تأخذ مكانها.", "en": "The current picture slides out to the left while the next picture enters from the right.", "fr": "L'image actuelle sort vers la gauche pendant que la suivante entre par la droite."}),
            ("slideright", {"ar": "انزلاق نحو اليمين", "en": "Slide right", "fr": "Glissement vers la droite"}, {"ar": "تتحرك الصورة الحالية كاملة نحو خارج الشاشة من اليمين، وفي الوقت نفسه تدخل الصورة التالية من اليسار حتى تأخذ مكانها.", "en": "The current picture slides out to the right while the next picture enters from the left.", "fr": "L'image actuelle sort vers la droite pendant que la suivante entre par la gauche."}),
            ("slideup", {"ar": "انزلاق نحو الأعلى", "en": "Slide up", "fr": "Glissement vers le haut"}, {"ar": "تتحرك الصورة الحالية كاملة إلى خارج الشاشة من الأعلى، وتدخل الصورة التالية من الأسفل حتى تأخذ مكانها.", "en": "The current picture slides upward out of the screen while the next picture enters from below.", "fr": "L'image actuelle sort par le haut pendant que la suivante entre par le bas."}),
            ("slidedown", {"ar": "انزلاق نحو الأسفل", "en": "Slide down", "fr": "Glissement vers le bas"}, {"ar": "تتحرك الصورة الحالية كاملة إلى خارج الشاشة من الأسفل، وتدخل الصورة التالية من الأعلى حتى تأخذ مكانها.", "en": "The current picture slides downward out of the screen while the next picture enters from above.", "fr": "L'image actuelle sort par le bas pendant que la suivante entre par le haut."}),
        ],
    },
    {
        "name": {"ar": "تغطية الصورة الحالية", "en": "Cover current picture", "fr": "Recouvrir l'image actuelle"},
        "effects": [
            ("coverleft", {"ar": "تغطية نحو اليسار", "en": "Cover left", "fr": "Recouvrir vers la gauche"}, {"ar": "تبقى الصورة الحالية ثابتة، بينما تدخل الصورة التالية من جهة اليمين وتتحرك نحو اليسار فوقها حتى تغطيها بالكامل.", "en": "The current picture stays still while the next picture enters from the right and covers it.", "fr": "L'image actuelle reste fixe pendant que la suivante entre par la droite et la recouvre."}),
            ("coverright", {"ar": "تغطية نحو اليمين", "en": "Cover right", "fr": "Recouvrir vers la droite"}, {"ar": "تبقى الصورة الحالية ثابتة، بينما تدخل الصورة التالية من جهة اليسار وتتحرك نحو اليمين فوقها حتى تغطيها بالكامل.", "en": "The current picture stays still while the next picture enters from the left and covers it.", "fr": "L'image actuelle reste fixe pendant que la suivante entre par la gauche et la recouvre."}),
            ("coverup", {"ar": "تغطية نحو الأعلى", "en": "Cover up", "fr": "Recouvrir vers le haut"}, {"ar": "تبقى الصورة الحالية ثابتة، بينما تدخل الصورة التالية من أسفل الشاشة وتتحرك نحو الأعلى فوقها حتى تغطيها بالكامل.", "en": "The current picture stays still while the next picture enters from below and covers it upward.", "fr": "L'image actuelle reste fixe pendant que la suivante entre par le bas et la recouvre vers le haut."}),
            ("coverdown", {"ar": "تغطية نحو الأسفل", "en": "Cover down", "fr": "Recouvrir vers le bas"}, {"ar": "تبقى الصورة الحالية ثابتة، بينما تدخل الصورة التالية من أعلى الشاشة وتتحرك نحو الأسفل فوقها حتى تغطيها بالكامل.", "en": "The current picture stays still while the next picture enters from above and covers it downward.", "fr": "L'image actuelle reste fixe pendant que la suivante entre par le haut et la recouvre vers le bas."}),
        ],
    },
    {
        "name": {"ar": "كشف الصورة التالية", "en": "Reveal next picture", "fr": "Reveler l'image suivante"},
        "effects": [
            ("revealleft", {"ar": "كشف نحو اليسار", "en": "Reveal left", "fr": "Reveler vers la gauche"}, {"ar": "تكون الصورة التالية ثابتة أسفل الصورة الحالية. تتحرك الصورة الحالية نحو اليسار وتخرج من الشاشة، فتظهر الصورة التالية من الجهة اليمنى.", "en": "The next picture is underneath. The current picture moves left and reveals it from the right.", "fr": "L'image suivante est dessous. L'image actuelle part vers la gauche et la revele depuis la droite."}),
            ("revealright", {"ar": "كشف نحو اليمين", "en": "Reveal right", "fr": "Reveler vers la droite"}, {"ar": "تكون الصورة التالية ثابتة أسفل الصورة الحالية. تتحرك الصورة الحالية نحو اليمين وتخرج من الشاشة، فتظهر الصورة التالية من الجهة اليسرى.", "en": "The next picture is underneath. The current picture moves right and reveals it from the left.", "fr": "L'image suivante est dessous. L'image actuelle part vers la droite et la revele depuis la gauche."}),
            ("revealup", {"ar": "كشف نحو الأعلى", "en": "Reveal up", "fr": "Reveler vers le haut"}, {"ar": "تكون الصورة التالية ثابتة أسفل الصورة الحالية. تتحرك الصورة الحالية إلى أعلى وتخرج من الشاشة، فتظهر الصورة التالية من الأسفل.", "en": "The next picture is underneath. The current picture moves upward and reveals it from below.", "fr": "L'image suivante est dessous. L'image actuelle monte et la revele depuis le bas."}),
            ("revealdown", {"ar": "كشف نحو الأسفل", "en": "Reveal down", "fr": "Reveler vers le bas"}, {"ar": "تكون الصورة التالية ثابتة أسفل الصورة الحالية. تتحرك الصورة الحالية إلى أسفل وتخرج من الشاشة، فتظهر الصورة التالية من الأعلى.", "en": "The next picture is underneath. The current picture moves downward and reveals it from above.", "fr": "L'image suivante est dessous. L'image actuelle descend et la revele depuis le haut."}),
        ],
    },
    {
        "name": {"ar": "الانتقالات الدائرية", "en": "Circular transitions", "fr": "Transitions circulaires"},
        "effects": [
            ("circleopen", {"ar": "فتح دائري من المنتصف", "en": "Circle open", "fr": "Ouverture circulaire"}, {"ar": "تظهر الصورة التالية أولا داخل دائرة صغيرة في منتصف الشاشة. تتسع الدائرة تدريجيا نحو الحواف حتى تغطي الصورة التالية الشاشة كاملة.", "en": "The next picture first appears inside a small circle in the center, then the circle expands to fill the screen.", "fr": "L'image suivante apparait dans un petit cercle au centre, puis le cercle s'agrandit jusqu'aux bords."}),
            ("circleclose", {"ar": "إغلاق دائري نحو المنتصف", "en": "Circle close", "fr": "Fermeture circulaire"}, {"ar": "تبدأ الصورة التالية بالظهور من حواف الشاشة. تنكمش الصورة الحالية داخل دائرة تتجه نحو المنتصف حتى تختفي تماما.", "en": "The next picture appears from the edges while the current picture shrinks into a circle toward the center.", "fr": "L'image suivante apparait par les bords pendant que l'image actuelle se reduit en cercle vers le centre."}),
            ("circlecrop", {"ar": "قص دائري عبر الأسود", "en": "Circle crop through black", "fr": "Decoupe circulaire par le noir"}, {"ar": "تنكمش الصورة الحالية داخل دائرة في منتصف الشاشة، وتصبح المنطقة خارج الدائرة سوداء. تختفي الدائرة، ثم تظهر الصورة التالية داخل دائرة تكبر حتى تملأ الشاشة.", "en": "The current picture shrinks inside a center circle with black around it, then the next picture opens from a growing circle.", "fr": "L'image actuelle se reduit dans un cercle central avec du noir autour, puis l'image suivante s'ouvre dans un cercle grandissant."}),
        ],
    },
    {
        "name": {"ar": "الانتقالات المستطيلة", "en": "Rectangular transitions", "fr": "Transitions rectangulaires"},
        "effects": [
            ("rectcrop", {"ar": "قص مستطيل عبر الأسود", "en": "Rectangle crop through black", "fr": "Decoupe rectangulaire par le noir"}, {"ar": "تنكمش الصورة الحالية داخل مستطيل في منتصف الشاشة، وتصبح المنطقة حوله سوداء. يختفي المستطيل، ثم تظهر الصورة التالية داخل مستطيل يكبر حتى يملأ الشاشة.", "en": "The current picture shrinks inside a center rectangle with black around it, then the next picture opens from a growing rectangle.", "fr": "L'image actuelle se reduit dans un rectangle central avec du noir autour, puis l'image suivante s'ouvre dans un rectangle grandissant."}),
            ("vertopen", {"ar": "فتح من خط رأسي في المنتصف", "en": "Vertical open", "fr": "Ouverture verticale"}, {"ar": "تظهر الصورة التالية عند خط رأسي في منتصف الشاشة، ثم تتسع المنطقة الجديدة في اتجاه اليمين واليسار حتى تغطي الشاشة كاملة.", "en": "The next picture appears from a vertical center line and expands left and right until it fills the screen.", "fr": "L'image suivante apparait depuis une ligne verticale centrale puis s'etend a gauche et a droite."}),
            ("vertclose", {"ar": "إغلاق نحو خط رأسي في المنتصف", "en": "Vertical close", "fr": "Fermeture verticale"}, {"ar": "تظهر الصورة التالية من جانبي الشاشة، بينما تنكمش الصورة الحالية من اليمين واليسار نحو خط رأسي في المنتصف حتى تختفي.", "en": "The next picture appears from both sides while the current picture closes toward a vertical center line.", "fr": "L'image suivante apparait des deux cotes pendant que l'actuelle se ferme vers une ligne verticale centrale."}),
            ("horzopen", {"ar": "فتح من خط أفقي في المنتصف", "en": "Horizontal open", "fr": "Ouverture horizontale"}, {"ar": "تظهر الصورة التالية عند خط أفقي في منتصف الشاشة، ثم تتسع المنطقة الجديدة نحو الأعلى والأسفل حتى تغطي الشاشة كاملة.", "en": "The next picture appears from a horizontal center line and expands upward and downward.", "fr": "L'image suivante apparait depuis une ligne horizontale centrale puis s'etend vers le haut et le bas."}),
            ("horzclose", {"ar": "إغلاق نحو خط أفقي في المنتصف", "en": "Horizontal close", "fr": "Fermeture horizontale"}, {"ar": "تظهر الصورة التالية من أعلى الشاشة وأسفلها، بينما تنكمش الصورة الحالية نحو خط أفقي في المنتصف حتى تختفي.", "en": "The next picture appears from the top and bottom while the current picture closes toward a horizontal center line.", "fr": "L'image suivante apparait par le haut et le bas pendant que l'actuelle se ferme vers une ligne horizontale centrale."}),
        ],
    },
    {
        "name": {"ar": "الانتقالات القطرية", "en": "Diagonal transitions", "fr": "Transitions diagonales"},
        "effects": [
            ("diagtl", {"ar": "انتقال قطري من أعلى اليسار", "en": "Diagonal from top left", "fr": "Diagonale depuis le haut gauche"}, {"ar": "تبدأ الصورة التالية من الزاوية العلوية اليسرى، ويتحرك حد قطري نحو الزاوية السفلية اليمنى حتى تستبدل الصورة الحالية بالكامل.", "en": "The next picture begins at the top-left corner and a diagonal edge moves toward the bottom-right corner.", "fr": "L'image suivante commence en haut a gauche et un bord diagonal avance vers le bas a droite."}),
            ("diagtr", {"ar": "انتقال قطري من أعلى اليمين", "en": "Diagonal from top right", "fr": "Diagonale depuis le haut droit"}, {"ar": "تبدأ الصورة التالية من الزاوية العلوية اليمنى، ويتحرك حد قطري نحو الزاوية السفلية اليسرى حتى تستبدل الصورة الحالية بالكامل.", "en": "The next picture begins at the top-right corner and a diagonal edge moves toward the bottom-left corner.", "fr": "L'image suivante commence en haut a droite et un bord diagonal avance vers le bas a gauche."}),
            ("diagbl", {"ar": "انتقال قطري من أسفل اليسار", "en": "Diagonal from bottom left", "fr": "Diagonale depuis le bas gauche"}, {"ar": "تبدأ الصورة التالية من الزاوية السفلية اليسرى، ويتحرك حد قطري نحو الزاوية العلوية اليمنى حتى تستبدل الصورة الحالية بالكامل.", "en": "The next picture begins at the bottom-left corner and a diagonal edge moves toward the top-right corner.", "fr": "L'image suivante commence en bas a gauche et un bord diagonal avance vers le haut a droite."}),
            ("diagbr", {"ar": "انتقال قطري من أسفل اليمين", "en": "Diagonal from bottom right", "fr": "Diagonale depuis le bas droit"}, {"ar": "تبدأ الصورة التالية من الزاوية السفلية اليمنى، ويتحرك حد قطري نحو الزاوية العلوية اليسرى حتى تستبدل الصورة الحالية بالكامل.", "en": "The next picture begins at the bottom-right corner and a diagonal edge moves toward the top-left corner.", "fr": "L'image suivante commence en bas a droite et un bord diagonal avance vers le haut a gauche."}),
        ],
    },
    {
        "name": {"ar": "المسح المتسع من الزوايا", "en": "Expanding corner wipe", "fr": "Balayage depuis les coins"},
        "effects": [
            ("wipetl", {"ar": "مسح من زاوية أعلى اليسار", "en": "Wipe from top left", "fr": "Balayage depuis le haut gauche"}, {"ar": "تظهر الصورة التالية من الزاوية العلوية اليسرى داخل مساحة مستطيلة تكبر نحو اليمين والأسفل حتى تغطي الشاشة.", "en": "The next picture appears from the top-left corner inside a rectangle that grows right and downward.", "fr": "L'image suivante apparait depuis le haut gauche dans un rectangle qui grandit vers la droite et le bas."}),
            ("wipetr", {"ar": "مسح من زاوية أعلى اليمين", "en": "Wipe from top right", "fr": "Balayage depuis le haut droit"}, {"ar": "تظهر الصورة التالية من الزاوية العلوية اليمنى داخل مساحة مستطيلة تكبر نحو اليسار والأسفل حتى تغطي الشاشة.", "en": "The next picture appears from the top-right corner inside a rectangle that grows left and downward.", "fr": "L'image suivante apparait depuis le haut droit dans un rectangle qui grandit vers la gauche et le bas."}),
            ("wipebl", {"ar": "مسح من زاوية أسفل اليسار", "en": "Wipe from bottom left", "fr": "Balayage depuis le bas gauche"}, {"ar": "تظهر الصورة التالية من الزاوية السفلية اليسرى داخل مساحة مستطيلة تكبر نحو اليمين والأعلى حتى تغطي الشاشة.", "en": "The next picture appears from the bottom-left corner inside a rectangle that grows right and upward.", "fr": "L'image suivante apparait depuis le bas gauche dans un rectangle qui grandit vers la droite et le haut."}),
            ("wipebr", {"ar": "مسح من زاوية أسفل اليمين", "en": "Wipe from bottom right", "fr": "Balayage depuis le bas droit"}, {"ar": "تظهر الصورة التالية من الزاوية السفلية اليمنى داخل مساحة مستطيلة تكبر نحو اليسار والأعلى حتى تغطي الشاشة.", "en": "The next picture appears from the bottom-right corner inside a rectangle that grows left and upward.", "fr": "L'image suivante apparait depuis le bas droit dans un rectangle qui grandit vers la gauche et le haut."}),
        ],
    },
    {
        "name": {"ar": "الانتقالات الخاصة", "en": "Special transitions", "fr": "Transitions speciales"},
        "effects": [
            ("radial", {"ar": "مسح دائري مثل عقرب الساعة", "en": "Clock wipe", "fr": "Balayage circulaire"}, {"ar": "يدور حد الانتقال حول مركز الشاشة مثل عقرب الساعة. تكشف حركته أجزاء متتالية من الصورة التالية حتى يكمل دورة وتظهر الصورة كاملة.", "en": "A transition edge rotates around the center like a clock hand and reveals the next picture in order.", "fr": "Un bord tourne autour du centre comme une aiguille d'horloge et revele progressivement l'image suivante."}),
            ("distance", {"ar": "انتقال حسب اختلاف الألوان", "en": "Color distance change", "fr": "Transition par difference de couleur"}, {"ar": "لا يتحرك خط ثابت عبر الشاشة. تتبدل مناطق الصورة تدريجيا بحسب مقدار اختلاف ألوانها عن الصورة التالية، فتظهر أجزاء متفرقة أولا ثم تكتمل الصورة الجديدة.", "en": "There is no fixed moving line. Areas change according to color difference until scattered parts complete the next picture.", "fr": "Il n'y a pas de ligne mobile fixe. Les zones changent selon la difference de couleur jusqu'a completer l'image suivante."}),
        ],
    },
]


def localized_value(value):
    language = get_language()
    if language in value:
        return value[language]
    return value.get("en") or value.get("ar") or next(iter(value.values()))


def all_transition_effects():
    effects = []
    for group in GROUPS:
        for key, names, descriptions in group["effects"]:
            effects.append({"key": key, "name": localized_value(names), "description": localized_value(descriptions)})
    return effects


def transition_effect_by_key(key):
    for effect in all_transition_effects():
        if effect["key"] == key:
            return effect
    return all_transition_effects()[0]


def transition_group_names():
    return [localized_value(group["name"]) for group in GROUPS]


def transition_effects_for_group(index):
    index = max(0, min(index, len(GROUPS) - 1))
    return [
        {"key": key, "name": localized_value(names), "description": localized_value(descriptions)}
        for key, names, descriptions in GROUPS[index]["effects"]
    ]


def transition_group_index_for_key(key):
    for group_index, group in enumerate(GROUPS):
        if any(effect_key == key for effect_key, names, descriptions in group["effects"]):
            return group_index
    return 0


class TransitionEffectsDialog(wx.Dialog):
    def __init__(self, parent, current_key="", current_duration=1.0):
        super().__init__(parent, title=tr("المؤثرات الانتقالية"), size=(720, 460))
        self.parent = parent
        self.selection = None
        self.current_key = current_key
        self.current_duration = current_duration

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        group_label = wx.StaticText(panel, label=tr("مجموعة المؤثرات الانتقالية"))
        self.group_choice = wx.Choice(panel, choices=transition_group_names())
        self.group_choice.SetName(tr("اختيار مجموعة المؤثرات الانتقالية"))

        effects_label = wx.StaticText(panel, label=tr("المؤثرات المتاحة في المجموعة"))
        self.effects_list = wx.ListBox(panel)
        self.effects_list.SetName(tr("قائمة المؤثرات الانتقالية في المجموعة المحددة"))

        self.description = wx.TextCtrl(panel, value="", style=wx.TE_MULTILINE | wx.TE_READONLY)
        self.description.SetName(tr("وصف المؤثر الانتقالي"))

        duration_label = wx.StaticText(panel, label=tr("مدة الانتقال بالثواني"))
        self.duration_choice = wx.Choice(panel, choices=[label for label, value in TRANSITION_DURATIONS])
        self.duration_choice.SetName(tr("مدة الانتقال بالثواني"))

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        apply_button = wx.Button(panel, label=tr("تطبيق تأثير الانتقال"))
        cancel_button = wx.Button(panel, label=tr("إلغاء"))
        apply_button.SetName(tr("تطبيق تأثير الانتقال"))
        cancel_button.SetName(tr("إلغاء"))
        apply_button.SetDefault()
        buttons.Add(apply_button, flag=wx.ALL, border=6)
        buttons.Add(cancel_button, flag=wx.ALL, border=6)

        main_sizer.Add(group_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        main_sizer.Add(self.group_choice, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)
        main_sizer.Add(effects_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        main_sizer.Add(self.effects_list, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)
        main_sizer.Add(self.description, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)
        main_sizer.Add(duration_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        main_sizer.Add(self.duration_choice, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)
        main_sizer.Add(buttons, flag=wx.ALIGN_CENTER | wx.BOTTOM, border=8)
        panel.SetSizer(main_sizer)

        self.group_choice.Bind(wx.EVT_CHOICE, self.on_group_changed)
        self.effects_list.Bind(wx.EVT_LISTBOX, self.on_effect_changed)
        self.effects_list.Bind(wx.EVT_SET_FOCUS, self.on_effect_focus)
        apply_button.Bind(wx.EVT_BUTTON, self.accept)
        cancel_button.Bind(wx.EVT_BUTTON, self.close)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        bind_dialog_keys(self, self.on_key, (wx.Choice,), preserve_navigation_keys=True)

        self.set_initial_selection()
        self.Centre()
        wx.CallAfter(self.group_choice.SetFocus)

    def set_initial_selection(self):
        group_index = transition_group_index_for_key(self.current_key)
        self.group_choice.SetSelection(group_index)
        self.refresh_effects()
        effects = transition_effects_for_group(group_index)
        effect_index = next((index for index, effect in enumerate(effects) if effect["key"] == self.current_key), 0)
        self.effects_list.SetSelection(effect_index)
        duration_index = min(
            range(len(TRANSITION_DURATIONS)),
            key=lambda index: abs(TRANSITION_DURATIONS[index][1] - float(self.current_duration or 1.0)),
        )
        self.duration_choice.SetSelection(duration_index)
        self.update_description(True)

    def selected_effect(self):
        group_index = self.group_choice.GetSelection()
        if group_index == wx.NOT_FOUND:
            group_index = 0
        effects = transition_effects_for_group(group_index)
        effect_index = self.effects_list.GetSelection()
        if effect_index == wx.NOT_FOUND or effect_index >= len(effects):
            effect_index = 0
        return effects[effect_index]

    def selected_duration(self):
        index = self.duration_choice.GetSelection()
        if index == wx.NOT_FOUND or index >= len(TRANSITION_DURATIONS):
            index = 1
        return TRANSITION_DURATIONS[index][1]

    def refresh_effects(self):
        self.effects_list.Clear()
        for effect in transition_effects_for_group(self.group_choice.GetSelection()):
            self.effects_list.Append(effect["name"])
        if self.effects_list.GetCount():
            self.effects_list.SetSelection(0)

    def update_description(self, quiet=False):
        effect = self.selected_effect()
        description = effect["description"]
        self.description.SetValue(description)
        if not quiet and hasattr(self.parent, "say"):
            self.parent.say(f"{effect['name']} {description}", wait_for_ui=False)

    def on_group_changed(self, event):
        self.refresh_effects()
        self.update_description(quiet=True)

    def on_effect_changed(self, event):
        self.update_description()

    def on_effect_focus(self, event):
        self.update_description()
        event.Skip()

    def accept(self, event=None):
        effect = self.selected_effect()
        self.selection = TransitionEffectSelection(
            key=effect["key"],
            name=effect["name"],
            description=effect["description"],
            duration=self.selected_duration(),
        )
        self.EndModal(wx.ID_OK)

    def close(self, event=None):
        self.EndModal(wx.ID_CANCEL)

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.close(event)
            return
        event.Skip()
