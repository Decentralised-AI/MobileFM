import logging
import torch
import data
import torchvision

from models import imagebind_model
from models.imagebind_model import ModalityType, load_module
from models import lora as LoRA

from torchvision import transforms
from datasets.hhar import hhar
from torch.utils.data import DataLoader

logging.basicConfig(level=logging.INFO, force=True)

lora = True
linear_probing = False
device = "cuda:0" if torch.cuda.is_available() else "cpu"
load_head_post_proc_finetuned = True

assert not (linear_probing and lora), \
            "Linear probing is a subset of LoRA training procedure for ImageBind. " \
            "Cannot set both linear_probing=True and lora=True. "

if lora and not load_head_post_proc_finetuned:
    # Hack: adjust lora_factor to the `max batch size used during training / temperature` to compensate missing norm
    lora_factor = 12 / 0.07
else:
    # This assumes proper loading of all params but results in shift from original dist in case of LoRA
    lora_factor = 1

text_list = ['Biking', 'Sitting', 'Standing', 'Walking', 'WALKING UPSTAIRS', 'WALKING DOWNSTAIRS']
text_list = ['a human is {}.'.format(text.lower()) for text in text_list]

# Instantiate model
model = imagebind_model.imagebind_huge(pretrained=True)
if lora:
    model.modality_trunks.update(
        LoRA.apply_lora_modality_trunks(model.modality_trunks, rank=4,
                                        layer_idxs=None,
                                        modality_names=[ModalityType.IMU, ModalityType.TEXT]))

    # Load LoRA params if found
    LoRA.load_lora_modality_trunks(model.modality_trunks,
                                   checkpoint_dir=".checkpoints/lora/500_hhar")

    if load_head_post_proc_finetuned:
        # Load postprocessors & heads
        load_module(model.modality_postprocessors, module_name="postprocessors",
                    checkpoint_dir=".checkpoints/lora/500_hhar")
        load_module(model.modality_heads, module_name="heads",
                    checkpoint_dir=".checkpoints/lora/500_hhar")
elif linear_probing:
    # Load heads
    load_module(model.modality_heads, module_name="heads",
                checkpoint_dir=".checkpoints/lora/500_hhar")

model.eval()
model.to(device)

test_ds = hhar(train=False)
test_dl = DataLoader(dataset=test_ds, batch_size=64, shuffle=False, drop_last=False,
    num_workers=4, pin_memory=True, persistent_workers=True)

def run_inference():
    test_correct = 0
    test_total = 0
    with torch.no_grad():
        for batch_idx, (x, target) in enumerate(test_dl):
            x = x.to(device)
            target = target.to(device)
            inputs = {
                ModalityType.IMU: x,
                ModalityType.TEXT: data.load_and_transform_text(text_list, device),
            }
            
            embeddings = model(inputs)
            match_value_1 = embeddings[ModalityType.IMU]@embeddings[ModalityType.TEXT].T * (lora_factor if lora else 1)
            result_1 = torch.softmax(match_value_1, dim=-1)
            _, predicted = torch.max(result_1, -1)
            correct = predicted.eq(target).sum()
            test_correct += correct.item()
            test_total += target.size(0)
            logging.info(f"batch_idx = {batch_idx}, test_correct = {test_correct}, test_total = {test_total}, Accuracy = {test_correct / test_total * 100:.3f}%")
    
    return test_correct / test_total

Accuracy = run_inference()

print("Model Performance:", Accuracy)
