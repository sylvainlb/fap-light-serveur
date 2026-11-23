"""Tests du schéma annexe A (extrait de la spec) — cas mode × cardinalité."""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.schema import validate_component  # noqa: E402

BASE = {
    "@context": {"cmp": "https://exemple.atelier/ns/component#", "app": "https://pod/ns/app/interventions#"},
    "@type": "cmp:AiComponent",
    "cmp:label": {"en": "X"},
    "cmp:package": "https://pod/apps/atelier/meta",
    "cmp:contract": "https://pod/apps/atelier/shapes",
    "cmp:source": "https://pod/apps/atelier/interventions/",
    "cmp:model": "ollama://mistral-small",
    "cmp:promptTemplate": "Sum {{app:report}}",
    "cmp:cardinality": "cmp:perResource",
    "cmp:outputMode": "cmp:text",
    "cmp:outputField": "app:summary",
    "cmp:provenanceFields": {k: "app:p_" + k for k in
                             ["cmp:modelField", "cmp:componentField", "cmp:promptVersionField",
                              "cmp:runDateField", "cmp:localeField"]},
    "cmp:promptVersion": "1.0",
}


class TestSchema(unittest.TestCase):
    def ok(self, doc):
        self.assertEqual(validate_component(doc), [], json.dumps(validate_component(doc)))

    def ko(self, doc):
        self.assertNotEqual(validate_component(doc), [])

    def test_texte_per_resource_valide(self):
        self.ok(copy.deepcopy(BASE))

    def test_sans_source_de_modele_rejet(self):
        d = copy.deepcopy(BASE); del d["cmp:package"]
        self.ko(d)

    def test_core_true_et_package_rejet(self):
        d = copy.deepcopy(BASE); d["cmp:core"] = True
        self.ko(d)

    def test_core_false_avec_package_valide(self):
        d = copy.deepcopy(BASE); d["cmp:core"] = False
        self.ok(d)

    def test_core_seul_valide(self):
        d = copy.deepcopy(BASE); d["cmp:core"] = True; del d["cmp:package"]
        self.ok(d)

    def test_texte_per_resource_cible_interdite(self):
        for champ in ("cmp:outputTarget", "cmp:outputPackage", "cmp:outputContract", "cmp:outputCore"):
            d = copy.deepcopy(BASE); d[champ] = "https://pod/x" if champ != "cmp:outputCore" else True
            self.ko(d)

    def test_texte_per_container_exige_cible_et_epinglage(self):
        d = copy.deepcopy(BASE); d["cmp:cardinality"] = "cmp:perContainer"
        self.ko(d)  # rien
        d["cmp:outputTarget"] = "https://pod/s/"; self.ko(d)  # pas de contrat
        d["cmp:outputContract"] = "https://pod/apps/atelier/shapes"; self.ko(d)  # pas d'épinglage source
        d["cmp:outputPackage"] = "https://pod/apps/atelier/meta"; self.ok(d)

    def test_epinglage_sortie_exclusif(self):
        d = copy.deepcopy(BASE); d["cmp:cardinality"] = "cmp:perContainer"
        d.update({"cmp:outputTarget": "https://pod/s/", "cmp:outputContract": "https://pod/s",
                  "cmp:outputCore": True, "cmp:outputPackage": "https://pod/m"})
        self.ko(d)

    def test_structure_interdit_output_field(self):
        d = copy.deepcopy(BASE); d["cmp:outputMode"] = "cmp:structured"
        d.update({"cmp:outputTarget": "https://pod/s/", "cmp:outputContract": "https://pod/s",
                  "cmp:outputPackage": "https://pod/m"})
        self.ko(d)  # outputField encore présent
        del d["cmp:outputField"]; self.ok(d)

    def test_parametres_bornes_et_fermes(self):
        d = copy.deepcopy(BASE); d["cmp:parameters"] = {"temperature": 3}
        self.ko(d)
        d["cmp:parameters"] = {"temperature": 0.2, "maxRetries": 2, "presencePenalty": 1}
        self.ko(d)
        d["cmp:parameters"] = {"temperature": 0.2, "maxOutputLength": 500, "maxRetries": 2}
        self.ok(d)

    def test_champ_inconnu_rejet(self):
        d = copy.deepcopy(BASE); d["cmp:extra"] = 1
        self.ko(d)

    def test_model_pattern_uri(self):
        d = copy.deepcopy(BASE); d["cmp:model"] = "pas un uri"
        self.ko(d)


if __name__ == "__main__":
    unittest.main()
