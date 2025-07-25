# Copyright 2021 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Testing suite for the PyTorch BEiT model."""

import unittest

from datasets import load_dataset

from transformers import BeitConfig
from transformers.testing_utils import (
    require_torch,
    require_torch_multi_gpu,
    require_vision,
    slow,
    torch_device,
)
from transformers.utils import (
    cached_property,
    is_torch_available,
    is_vision_available,
)

from ...test_backbone_common import BackboneTesterMixin
from ...test_configuration_common import ConfigTester
from ...test_modeling_common import ModelTesterMixin, _config_zero_init, floats_tensor, ids_tensor
from ...test_pipeline_mixin import PipelineTesterMixin


if is_torch_available():
    import torch
    from torch import nn

    from transformers import (
        BeitBackbone,
        BeitForImageClassification,
        BeitForMaskedImageModeling,
        BeitForSemanticSegmentation,
        BeitModel,
    )
    from transformers.models.auto.modeling_auto import MODEL_FOR_BACKBONE_MAPPING_NAMES, MODEL_MAPPING_NAMES


if is_vision_available():
    from PIL import Image

    from transformers import BeitImageProcessor


class BeitModelTester:
    def __init__(
        self,
        parent,
        vocab_size=100,
        batch_size=13,
        image_size=30,
        patch_size=2,
        num_channels=3,
        is_training=True,
        use_labels=True,
        hidden_size=32,
        num_hidden_layers=4,
        num_attention_heads=4,
        intermediate_size=37,
        hidden_act="gelu",
        hidden_dropout_prob=0.1,
        attention_probs_dropout_prob=0.1,
        type_sequence_label_size=10,
        initializer_range=0.02,
        num_labels=3,
        scope=None,
        out_indices=[1, 2, 3, 4],
        out_features=["stage1", "stage2", "stage3", "stage4"],
        attn_implementation="eager",
        mask_ratio=0.5,
    ):
        self.parent = parent
        self.vocab_size = vocab_size
        self.batch_size = batch_size
        self.image_size = image_size
        self.patch_size = patch_size
        self.num_channels = num_channels
        self.is_training = is_training
        self.use_labels = use_labels
        self.hidden_size = hidden_size
        self.num_hidden_layers = num_hidden_layers
        self.num_attention_heads = num_attention_heads
        self.intermediate_size = intermediate_size
        self.hidden_act = hidden_act
        self.hidden_dropout_prob = hidden_dropout_prob
        self.attention_probs_dropout_prob = attention_probs_dropout_prob
        self.type_sequence_label_size = type_sequence_label_size
        self.initializer_range = initializer_range
        self.scope = scope
        self.out_indices = out_indices
        self.out_features = out_features
        self.num_labels = num_labels

        # in BeiT, the seq length equals the number of patches + 1 (we add 1 for the [CLS] token)
        num_patches = (image_size // patch_size) ** 2
        self.seq_length = num_patches + 1
        self.mask_length = self.seq_length - 1
        self.num_masks = int(mask_ratio * self.seq_length)
        self.attn_implementation = attn_implementation

    def prepare_config_and_inputs(self):
        pixel_values = floats_tensor([self.batch_size, self.num_channels, self.image_size, self.image_size])

        labels = None
        pixel_labels = None
        if self.use_labels:
            labels = ids_tensor([self.batch_size], self.type_sequence_label_size)
            pixel_labels = ids_tensor([self.batch_size, self.image_size, self.image_size], self.num_labels)

        config = self.get_config()

        return config, pixel_values, labels, pixel_labels

    def get_config(self):
        return BeitConfig(
            vocab_size=self.vocab_size,
            image_size=self.image_size,
            patch_size=self.patch_size,
            num_channels=self.num_channels,
            hidden_size=self.hidden_size,
            num_hidden_layers=self.num_hidden_layers,
            num_attention_heads=self.num_attention_heads,
            intermediate_size=self.intermediate_size,
            hidden_act=self.hidden_act,
            hidden_dropout_prob=self.hidden_dropout_prob,
            attention_probs_dropout_prob=self.attention_probs_dropout_prob,
            is_decoder=False,
            initializer_range=self.initializer_range,
            out_indices=self.out_indices,
            out_features=self.out_features,
            attn_implementation=self.attn_implementation,
        )

    def create_and_check_model(self, config, pixel_values, labels, pixel_labels):
        model = BeitModel(config=config)
        model.to(torch_device)
        model.eval()
        result = model(pixel_values)
        self.parent.assertEqual(result.last_hidden_state.shape, (self.batch_size, self.seq_length, self.hidden_size))

    def create_and_check_backbone(self, config, pixel_values, labels, pixel_labels):
        model = BeitBackbone(config=config)
        model.to(torch_device)
        model.eval()
        result = model(pixel_values)

        # verify hidden states
        self.parent.assertEqual(len(result.feature_maps), len(config.out_features))
        expected_height = expected_width = self.image_size // config.patch_size
        self.parent.assertListEqual(
            list(result.feature_maps[0].shape), [self.batch_size, self.hidden_size, expected_height, expected_width]
        )

        # verify channels
        self.parent.assertEqual(len(model.channels), len(config.out_features))

        # verify backbone works with out_features=None
        config.out_features = None
        model = BeitBackbone(config=config)
        model.to(torch_device)
        model.eval()
        result = model(pixel_values)

        # verify feature maps
        self.parent.assertEqual(len(result.feature_maps), 1)
        self.parent.assertListEqual(
            list(result.feature_maps[0].shape), [self.batch_size, self.hidden_size, expected_height, expected_width]
        )

        # verify channels
        self.parent.assertEqual(len(model.channels), 1)

    def create_and_check_for_masked_lm(self, config, pixel_values, labels, pixel_labels):
        model = BeitForMaskedImageModeling(config=config)
        model.to(torch_device)
        model.eval()
        result = model(pixel_values)
        self.parent.assertEqual(result.logits.shape, (self.batch_size, self.seq_length - 1, self.vocab_size))

    def create_and_check_for_image_classification(self, config, pixel_values, labels, pixel_labels):
        config.num_labels = self.type_sequence_label_size
        model = BeitForImageClassification(config)
        model.to(torch_device)
        model.eval()
        result = model(pixel_values, labels=labels)
        self.parent.assertEqual(result.logits.shape, (self.batch_size, self.type_sequence_label_size))

        # test greyscale images
        config.num_channels = 1
        model = BeitForImageClassification(config)
        model.to(torch_device)
        model.eval()

        pixel_values = floats_tensor([self.batch_size, 1, self.image_size, self.image_size])
        result = model(pixel_values, labels=labels)
        self.parent.assertEqual(result.logits.shape, (self.batch_size, self.type_sequence_label_size))

    def create_and_check_for_semantic_segmentation(self, config, pixel_values, labels, pixel_labels):
        config.num_labels = self.num_labels
        model = BeitForSemanticSegmentation(config)
        model.to(torch_device)
        model.eval()
        result = model(pixel_values)
        self.parent.assertEqual(
            result.logits.shape, (self.batch_size, self.num_labels, self.image_size * 2, self.image_size * 2)
        )
        result = model(pixel_values, labels=pixel_labels)
        self.parent.assertEqual(
            result.logits.shape, (self.batch_size, self.num_labels, self.image_size * 2, self.image_size * 2)
        )

    def prepare_config_and_inputs_for_common(self):
        config_and_inputs = self.prepare_config_and_inputs()
        config, pixel_values, labels, pixel_labels = config_and_inputs
        inputs_dict = {"pixel_values": pixel_values}
        return config, inputs_dict


@require_torch
class BeitModelTest(ModelTesterMixin, PipelineTesterMixin, unittest.TestCase):
    """
    Here we also overwrite some of the tests of test_modeling_common.py, as BEiT does not use input_ids, inputs_embeds,
    attention_mask and seq_length.
    """

    all_model_classes = (
        (
            BeitModel,
            BeitForImageClassification,
            BeitForMaskedImageModeling,
            BeitForSemanticSegmentation,
            BeitBackbone,
        )
        if is_torch_available()
        else ()
    )
    pipeline_model_mapping = (
        {
            "image-feature-extraction": BeitModel,
            "image-classification": BeitForImageClassification,
            "image-segmentation": BeitForSemanticSegmentation,
        }
        if is_torch_available()
        else {}
    )

    test_pruning = False
    test_resize_embeddings = False
    test_head_masking = False
    test_torch_exportable = True

    def setUp(self):
        self.model_tester = BeitModelTester(self)
        self.config_tester = ConfigTester(self, config_class=BeitConfig, has_text_modality=False, hidden_size=37)

    def test_config(self):
        self.config_tester.run_common_tests()

    @unittest.skip(reason="BEiT does not use inputs_embeds")
    def test_inputs_embeds(self):
        pass

    @require_torch_multi_gpu
    @unittest.skip(reason="BEiT has some layers using `add_module` which doesn't work well with `nn.DataParallel`")
    def test_multi_gpu_data_parallel_forward(self):
        pass

    @unittest.skip(reason="BEiT does not support feedforward chunking yet")
    def test_feed_forward_chunking(self):
        pass

    @unittest.skip(reason="BEiT can't compile dynamic")
    def test_sdpa_can_compile_dynamic(self):
        pass

    def test_model_get_set_embeddings(self):
        config, _ = self.model_tester.prepare_config_and_inputs_for_common()

        for model_class in self.all_model_classes:
            model = model_class(config)
            self.assertIsInstance(model.get_input_embeddings(), (nn.Module))
            x = model.get_output_embeddings()
            self.assertTrue(x is None or isinstance(x, nn.Linear))

    def test_model(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_model(*config_and_inputs)

    def test_backbone(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_backbone(*config_and_inputs)

    def test_for_masked_lm(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_for_masked_lm(*config_and_inputs)

    def test_for_image_classification(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_for_image_classification(*config_and_inputs)

    def test_for_semantic_segmentation(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_for_semantic_segmentation(*config_and_inputs)

    def test_training(self):
        if not self.model_tester.is_training:
            self.skipTest(reason="model_tester.is_training is set to False")

        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()
        config.return_dict = True

        for model_class in self.all_model_classes:
            # we don't test BeitForMaskedImageModeling
            if model_class.__name__ in [
                *MODEL_MAPPING_NAMES.values(),
                *MODEL_FOR_BACKBONE_MAPPING_NAMES.values(),
                "BeitForMaskedImageModeling",
            ]:
                continue

            model = model_class(config)
            model.to(torch_device)
            model.train()
            inputs = self._prepare_for_class(inputs_dict, model_class, return_labels=True)
            loss = model(**inputs).loss
            loss.backward()

    def test_training_gradient_checkpointing(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()
        if not self.model_tester.is_training:
            self.skipTest(reason="model_tester.is_training is set to False")

        config.use_cache = False
        config.return_dict = True

        for model_class in self.all_model_classes:
            # we don't test BeitForMaskedImageModeling
            if (
                model_class.__name__
                in [
                    *MODEL_MAPPING_NAMES.values(),
                    *MODEL_FOR_BACKBONE_MAPPING_NAMES.values(),
                    "BeitForMaskedImageModeling",
                ]
                or not model_class.supports_gradient_checkpointing
            ):
                continue

            model = model_class(config)
            model.gradient_checkpointing_enable()
            model.to(torch_device)
            model.train()
            inputs = self._prepare_for_class(inputs_dict, model_class, return_labels=True)
            loss = model(**inputs).loss
            loss.backward()

    @unittest.skip(
        reason="This architecture seem to not compute gradients properly when using GC, check: https://github.com/huggingface/transformers/pull/27124"
    )
    def test_training_gradient_checkpointing_use_reentrant(self):
        pass

    @unittest.skip(
        reason="This architecture seem to not compute gradients properly when using GC, check: https://github.com/huggingface/transformers/pull/27124"
    )
    def test_training_gradient_checkpointing_use_reentrant_false(self):
        pass

    def test_initialization(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()

        configs_no_init = _config_zero_init(config)
        for model_class in self.all_model_classes:
            model = model_class(config=configs_no_init)
            for name, param in model.named_parameters():
                # we skip lambda parameters as these require special initial values
                # determined by config.layer_scale_init_value
                if "lambda" in name:
                    continue
                if param.requires_grad:
                    self.assertIn(
                        ((param.data.mean() * 1e9).round() / 1e9).item(),
                        [0.0, 1.0],
                        msg=f"Parameter {name} of model {model_class} seems not properly initialized",
                    )

    @slow
    def test_model_from_pretrained(self):
        model_name = "microsoft/beit-base-patch16-224"
        model = BeitModel.from_pretrained(model_name)
        self.assertIsNotNone(model)


# We will verify our results on an image of cute cats
def prepare_img():
    image = Image.open("./tests/fixtures/tests_samples/COCO/000000039769.png")
    return image


@require_torch
@require_vision
class BeitModelIntegrationTest(unittest.TestCase):
    @cached_property
    def default_image_processor(self):
        return BeitImageProcessor.from_pretrained("microsoft/beit-base-patch16-224") if is_vision_available() else None

    @slow
    def test_inference_masked_image_modeling_head(self):
        model = BeitForMaskedImageModeling.from_pretrained("microsoft/beit-base-patch16-224-pt22k").to(torch_device)

        image_processor = self.default_image_processor
        image = prepare_img()
        pixel_values = image_processor(images=image, return_tensors="pt").pixel_values.to(torch_device)

        # prepare bool_masked_pos
        bool_masked_pos = torch.ones((1, 196), dtype=torch.bool).to(torch_device)

        # forward pass
        with torch.no_grad():
            outputs = model(pixel_values=pixel_values, bool_masked_pos=bool_masked_pos)
        logits = outputs.logits

        # verify the logits
        expected_shape = torch.Size((1, 196, 8192))
        self.assertEqual(logits.shape, expected_shape)

        expected_slice = torch.tensor(
            [[-3.2437, 0.5072, -13.9174], [-3.2456, 0.4948, -13.9401], [-3.2033, 0.5121, -13.8550]]
        ).to(torch_device)

        torch.testing.assert_close(logits[bool_masked_pos][:3, :3], expected_slice, rtol=1e-2, atol=1e-2)

    @slow
    def test_inference_image_classification_head_imagenet_1k(self):
        model = BeitForImageClassification.from_pretrained("microsoft/beit-base-patch16-224").to(torch_device)

        image_processor = self.default_image_processor
        image = prepare_img()
        inputs = image_processor(images=image, return_tensors="pt").to(torch_device)

        # forward pass
        with torch.no_grad():
            outputs = model(**inputs)
        logits = outputs.logits

        # verify the logits
        expected_shape = torch.Size((1, 1000))
        self.assertEqual(logits.shape, expected_shape)

        expected_slice = torch.tensor([-1.2385, -1.0987, -1.0108]).to(torch_device)

        torch.testing.assert_close(logits[0, :3], expected_slice, rtol=1e-4, atol=1e-4)

        expected_class_idx = 281
        self.assertEqual(logits.argmax(-1).item(), expected_class_idx)

    @slow
    def test_inference_image_classification_head_imagenet_22k(self):
        model = BeitForImageClassification.from_pretrained("microsoft/beit-large-patch16-224-pt22k-ft22k").to(
            torch_device
        )

        image_processor = self.default_image_processor
        image = prepare_img()
        inputs = image_processor(images=image, return_tensors="pt").to(torch_device)

        # forward pass
        with torch.no_grad():
            outputs = model(**inputs)
        logits = outputs.logits

        # verify the logits
        expected_shape = torch.Size((1, 21841))
        self.assertEqual(logits.shape, expected_shape)

        expected_slice = torch.tensor([1.6881, -0.2787, 0.5901]).to(torch_device)

        torch.testing.assert_close(logits[0, :3], expected_slice, rtol=1e-4, atol=1e-4)

        expected_class_idx = 2396
        self.assertEqual(logits.argmax(-1).item(), expected_class_idx)

    @slow
    def test_inference_semantic_segmentation(self):
        model = BeitForSemanticSegmentation.from_pretrained("microsoft/beit-base-finetuned-ade-640-640")
        model = model.to(torch_device)

        image_processor = BeitImageProcessor(do_resize=True, size=640, do_center_crop=False)

        ds = load_dataset("hf-internal-testing/fixtures_ade20k", split="test")
        image = ds[0]["image"].convert("RGB")
        inputs = image_processor(images=image, return_tensors="pt").to(torch_device)

        # forward pass
        with torch.no_grad():
            outputs = model(**inputs)
        logits = outputs.logits

        # verify the logits
        expected_shape = torch.Size((1, 150, 160, 160))
        self.assertEqual(logits.shape, expected_shape)

        expected_slice = torch.tensor(
            [
                [[-4.8963, -2.3696, -3.0359], [-2.8485, -0.9842, -1.7426], [-2.9453, -1.3338, -2.1463]],
                [[-5.8099, -3.4140, -4.1025], [-3.8578, -2.2100, -3.0337], [-3.8383, -2.4615, -3.3681]],
                [[-0.0314, 3.9864, 4.0536], [2.9637, 4.6879, 4.9976], [3.2074, 4.7690, 4.9946]],
            ],
            device=torch_device,
        )
        torch.testing.assert_close(logits[0, :3, :3, :3], expected_slice, rtol=1e-4, atol=1e-4)

    @slow
    def test_post_processing_semantic_segmentation(self):
        model = BeitForSemanticSegmentation.from_pretrained("microsoft/beit-base-finetuned-ade-640-640")
        model = model.to(torch_device)

        image_processor = BeitImageProcessor(do_resize=True, size=640, do_center_crop=False)

        ds = load_dataset("hf-internal-testing/fixtures_ade20k", split="test")
        image = ds[0]["image"].convert("RGB")
        inputs = image_processor(images=image, return_tensors="pt").to(torch_device)

        # forward pass
        with torch.no_grad():
            outputs = model(**inputs)

        outputs.logits = outputs.logits.detach().cpu()

        segmentation = image_processor.post_process_semantic_segmentation(outputs=outputs, target_sizes=[(500, 300)])
        expected_shape = torch.Size((500, 300))
        self.assertEqual(segmentation[0].shape, expected_shape)

        segmentation = image_processor.post_process_semantic_segmentation(outputs=outputs)
        expected_shape = torch.Size((160, 160))
        self.assertEqual(segmentation[0].shape, expected_shape)

    @slow
    def test_inference_interpolate_pos_encoding(self):
        model_name = "microsoft/beit-base-patch16-224-pt22k"
        model = BeitModel.from_pretrained(model_name, **{"use_absolute_position_embeddings": True}).to(torch_device)

        image = Image.open("./tests/fixtures/tests_samples/COCO/000000039769.png")
        processor = BeitImageProcessor.from_pretrained(model_name)
        inputs = processor(images=image, return_tensors="pt", size={"height": 480, "width": 480})
        pixel_values = inputs.pixel_values.to(torch_device)

        # with interpolate_pos_encoding being True the model should process the higher resolution image
        # successfully and produce the expected output.
        with torch.no_grad():
            outputs = model(pixel_values, interpolate_pos_encoding=True)

        # num_cls_tokens + (height / patch_size) * (width / patch_size)
        # 1 + (480 / 16) * (480 / 16) = 1 + 30 * 30 = 901
        expected_shape = torch.Size((1, 901, 768))
        self.assertEqual(outputs.last_hidden_state.shape, expected_shape)


@require_torch
class BeitBackboneTest(unittest.TestCase, BackboneTesterMixin):
    all_model_classes = (BeitBackbone,) if is_torch_available() else ()
    config_class = BeitConfig

    def setUp(self):
        self.model_tester = BeitModelTester(self)
