"""Train the linear model on weak labels (+ seed prototypes) from train_sample.jsonl."""
import collections
import json

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_predict

from features import doc_string, make_vectorizer
from weak_labels import SEEDS, weak_label

rows = [json.loads(l) for l in open("train_sample.jsonl", encoding="utf-8")]
docs, y = [], []
abstained = 0
for r in rows:
    lab = weak_label(r["name"], r["pages"], r["text"])
    if lab is None:
        abstained += 1
        continue
    docs.append(doc_string(r["name"], r["text"]))
    y.append(lab)
n_corpus = len(y)
for lab, texts in SEEDS.items():
    for i, t in enumerate(texts):
        for cut in (len(t), len(t) * 2 // 3):          # light augmentation
            docs.append(doc_string(f"seed{i}.pdf", t[:cut]))
            y.append(lab)
y = np.array(y)
print(f"rows={len(rows)} weak-labelled={n_corpus} abstained={abstained}")
print("label counts:", dict(collections.Counter(y)))

X = make_vectorizer().transform(docs)
clf = LogisticRegression(C=20, max_iter=300, class_weight="balanced")
pred = cross_val_predict(clf, X, y, cv=5)
print(f"5-fold agreement with weak labels: {(pred == y).mean():.4f}")
clf.fit(X, y)
joblib.dump(clf, "model.joblib")
print("saved model.joblib; classes:", list(clf.classes_))
