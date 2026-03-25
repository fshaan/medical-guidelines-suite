from scripts.batch_pipeline import _split_patients

def test_split_exact_division(sample_patients):
    """12 患者 / 4 = 3 批"""
    batches = _split_patients(sample_patients, batch_size=4)
    assert len(batches) == 3
    assert all(len(b) == 4 for b in batches)

def test_split_remainder(sample_patients):
    """12 患者 / 5 = 2 批(5) + 1 批(2)"""
    batches = _split_patients(sample_patients, batch_size=5)
    assert len(batches) == 3
    assert len(batches[0]) == 5
    assert len(batches[2]) == 2

def test_split_single_batch():
    """3 患者 / 5 = 1 批"""
    patients = [{"patient_id": f"P{i}"} for i in range(3)]
    batches = _split_patients(patients, batch_size=5)
    assert len(batches) == 1
    assert len(batches[0]) == 3

def test_split_empty():
    batches = _split_patients([], batch_size=5)
    assert batches == []
