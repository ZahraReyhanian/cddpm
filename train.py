import os
import json
import torch

from typing import Tuple
import pytorch_lightning as pl
from pytorch_lightning.strategies.ddp import DDPStrategy
# from pytorch_lightning.loggers.wandb import WandbLogger
# from utils.training_utils import log_hyperparameters
from Trainer import Trainer as MyModelTrainer
from utils import os_utils
from utils.callbacks import create_list_of_callbacks
from datamodules.face_datamodule import FaceDataModule


epochs=100
n_steps=1000

unet_config = {
    "freeze_unet": False,
    'model_params':
            {"image_size": 112,
             "num_channels": 128,
             "num_res_blocks": 1,
             "channel_mult": '',
             "learn_sigma": True,
             "class_cond": False,
             "use_checkpoint": False,
             "attention_resolutions": "16",
             "num_heads": 4,
             "num_head_channels": 64,
             "num_heads_upsample": -1,
             "use_scale_shift_norm": True,
             "dropout": 0.0,
             "resblock_updown": True,
             "use_fp16": False,
             "use_new_attention_order": False
             },
    'params':
        {"gradient_checkpointing": True,
         "condition_type": 'crossatt_and_stylemod',
         "condition_source": 'patchstat_spatial_and_image',
         "cross_attention_dim'": 512,
         'image_size': 112,
         'in_channels': 3,
         'out_channels': 3,
         'pretrained_model_path': '/opt/data/reyhanian/pretrained_models/ffhq_10m.pt'}
}



label_mapping = {
    'version': 'v4', 'out_channel': 256, 'num_latent': 8,
        'recognition_config':
            {'backbone': 'ir_50',
            'dataset': 'webface4m',
            'loss_fn': 'adaface',
            'normalize_feature': False,
            'return_spatial': [21],
            'head_name': 'none',
            'ckpt_path': None,
            'center_path': None}
}

recognition = {
    'backbone': 'ir_50',
    'dataset': 'webface4m',
    'loss_fn': 'adaface',
    'normalize_feature': False,
    'return_spatial': [2],
    'head_name': 'none',
    'ckpt_path': '/opt/data/reyhanian/pretrained_models/adaface_ir50_casia.ckpt',
    'center_path': '/opt/data/reyhanian/pretrained_models/center_ir_50_adaface_casia_faces_webface_112x112.pth'
}


recognition_eval ={
    'backbone': 'ir_101',
    'dataset': 'webface4m',
    'loss_fn': 'adaface',
    'normalize_feature': False,
    'return_spatial': [2],
    'head_name': 'none',
    'ckpt_path': None,
    'center_path': '/opt/data/reyhanian/pretrained_models/center_ir_101_adaface_webface4m_faces_webface_112x112.pth'
}


sampler= {
    "num_train_timesteps": n_steps,
    "beta_start": 0.0001,
    "beta_end": 0.02,
    "variance_type": "learned_range"
}

external_mapping= {
    "version": "v4_dropout",
    "return_spatial": [2],
    "spatial_dim": 5,
    "out_channel": 512,
    "dropout_prob": 0.3
}

def training(cfg):
    """Trains the model. Can additionally evaluate on a testset, using best weights obtained during
    training.

    This method is wrapped in optional @task_wrapper decorator which applies extra utilities
    before and after the call.

    Args:
        cfg (DictConfig): Configuration composed by Hydra.

    Returns:
        Tuple[dict, dict]: Dict with metrics and dict with all instantiated objects.
    """

    # set seed for random number generators in pytorch, numpy and python.random
    pl.seed_everything(cfg["seed"], workers=True)
    path = cfg["dataset_path"]

    datamodule = FaceDataModule(dataset_path=path, img_size=(cfg["image_size"], cfg["image_size"]), batch_size=cfg["batch_size"])

    model = MyModelTrainer(unet_config=unet_config,
                           # ckpt_path=cfg["ckpt_path"],
                           lr=cfg['lr'],
                           recognition=recognition,
                           recognition_eval=recognition_eval,
                           label_mapping= label_mapping,
                           external_mapping=external_mapping,
                           output_dir=cfg["output_dir"],
                           mse_loss_lambda=cfg["mse_loss_lambda"],
                           identity_consistency_loss_lambda=cfg["identity_consistency_loss_lambda"],
                           sampler=sampler)

    print("Instantiating callbacks...")
    callbacks = create_list_of_callbacks(cfg["ckpt_path"])

    # print("Instantiating loggers...")
    # logger = WandbLogger(project=cfg["project_task"], log_model='all', id= cfg["id"], save_dir=cfg["output_dir"],)
    print("before train.....................................................................")
    strategy = DDPStrategy(find_unused_parameters=True)
    trainer = pl.Trainer(accelerator="gpu", callbacks=callbacks, strategy=strategy, max_epochs=epochs)

    object_dict = {
        "cfg": cfg,
        "datamodule": datamodule,
        "model": model,
        "callbacks": callbacks,
        # "logger": logger,
        "trainer": trainer,
    }


    if cfg["training"]:
        print("Starting training...")
        if cfg["ckpt_path"]:
            print('continuing from ', cfg["ckpt_path"])

        trainer.fit(model=model, datamodule=datamodule)
        trainer.save_checkpoint(f"{cfg['ckpt_path']}/final.ckpt")

    train_metrics = trainer.callback_metrics

    if cfg.get("test"):
        print("Starting testing!")
        if cfg.get("ckpt_path") and not cfg.get("train"):
            print("Using predefined ckpt_path", cfg.get('ckpt_path'))
            ckpt_path = cfg.get("ckpt_path")
        elif cfg.get('trainer')['ckpt_path'] and not cfg.get("train"):
            print('Model weight will be loaded during Making the Model')
            ckpt_path = None
        else:
            ckpt_path = trainer.checkpoint_callback.best_model_path

        if ckpt_path == "":
            print("Best ckpt not found! Using current weights for testing...")
            raise ValueError('')
        trainer.test(model=model, datamodule=datamodule, ckpt_path=ckpt_path)
        print(f"Best ckpt path: {ckpt_path}")

    test_metrics = trainer.callback_metrics

    # merge train and test metrics
    metric_dict = {**train_metrics, **test_metrics}

    return metric_dict, object_dict


def main():
    with open('config/config.json') as f:
        cfg = json.load(f)

    print(f"tmp directory : {cfg['output_dir']}")
    #TODO: if exits remove
    # if 'experiments' in cfg.output_dir:
    #     print(f"removing tmp directory : {cfg.output_dir}")
    #     shutil.rmtree(cfg.paths.output_dir, ignore_errors=True)
    run_name = os_utils.make_runname(cfg["prefix"])
    task = os.path.basename(cfg["project_task"])
    exp_root = os.path.dirname(cfg["output_dir"])
    output_dir = os_utils.make_output_dir(exp_root, task, run_name)
    os.makedirs(output_dir, exist_ok=True)

    wandb_name = os.path.basename(cfg["output_dir"])

    print(f"Current working directory : {os.getcwd()}")
    print(f"Saving Directory          : {cfg['output_dir']}")
    available_gpus = torch.cuda.device_count()
    print("available_gpus------------", available_gpus)
    # cfg.datamodule.batch_size = int(cfg.datamodule.total_gpu_batch_size / available_gpus)
    # print('Per GPU batchsize:', cfg.datamodule.batch_size)
    # time.sleep(1)

    # train the model
    metric_dict, _ = training(cfg)
    print(metric_dict)


if __name__ == "__main__":
    main()