"""A tiny demo ``typantic[web]`` app — a runnable example (and docs/screenshots).

It defines two commands whose settings models become both a CLI (via
``add_command``) and a web form (via ``typantic web serve``). ``detect`` streams
a leveled log and writes colourful PNGs, so the dashboard's log tail and image
gallery have something real to show; every field has a default, so a job can be
launched with one click.

Install and use it standalone::

    pip install ./examples/typantic_demo 'typantic[web]'
    typantic web serve            # discovers "Detect objects" and "Train a model"
    # or just run the CLI:
    typantic-demo detect --images 12
"""

import logging
import random
import time
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import BaseModel, Field

from typantic import add_command

log = logging.getLogger("demo")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s  %(asctime)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


class DetectSettings(BaseModel):
    """Run a (simulated) detection over a folder of images."""

    output_dir: Annotated[
        Path,
        Field(default=Path(), description="Where result images are written."),
    ]
    preset: Annotated[
        Literal["fast", "balanced", "accurate"],
        Field(default="balanced", description="Speed / quality preset."),
    ]
    threshold: Annotated[
        float,
        Field(
            default=0.5, ge=0.0, le=1.0, description="Detection confidence threshold."
        ),
    ]
    images: Annotated[
        int,
        Field(default=8, ge=1, le=48, description="How many demo images to render."),
    ]
    tags: Annotated[
        list[str],
        Field(
            default_factory=lambda: ["demo"], description="Freeform tags for this run."
        ),
    ]
    save_overlays: Annotated[
        bool,
        Field(default=True, description="Write annotated overlay images."),
    ]
    seed: Annotated[
        int | None,
        Field(default=None, description="Random seed (optional)."),
    ]


def _run_detect(cfg: DetectSettings) -> None:
    _setup_logging()
    from PIL import Image, ImageDraw

    log.info(
        "Starting detection (preset=%s, threshold=%.2f)",
        cfg.preset,
        cfg.threshold,
    )
    log.info("Tags: %s", ", ".join(cfg.tags) or "(none)")
    out = cfg.output_dir
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(cfg.seed)
    time.sleep(1.5)
    for i in range(cfg.images):
        image = Image.new("RGB", (256, 256), (11, 15, 20))
        draw = ImageDraw.Draw(image)
        count = rng.randint(6, 24)
        for _ in range(count):
            x, y = rng.randint(0, 256), rng.randint(0, 256)
            radius = rng.randint(10, 46)
            color = (
                rng.randint(40, 255),
                rng.randint(40, 255),
                rng.randint(40, 255),
            )
            draw.ellipse(
                [x - radius, y - radius, x + radius, y + radius],
                outline=color,
                width=3,
            )
        if cfg.save_overlays:
            image.save(out / f"detection_{i:02d}.png")
        log.info("Frame %02d: %d detections", i, count)
        time.sleep(0.35)
    log.warning("3 detections below the %.2f threshold were dropped", cfg.threshold)
    written = cfg.images if cfg.save_overlays else 0
    log.info("Done - wrote %d overlays to %s", written, out)


class TrainSettings(BaseModel):
    """Train a (simulated) model; streams per-epoch loss."""

    dataset: Annotated[
        Path,
        Field(default=Path(), description="Dataset folder."),
    ]
    epochs: Annotated[
        int,
        Field(default=12, ge=1, le=1000, description="Training epochs."),
    ]
    lr: Annotated[
        float,
        Field(default=1e-3, gt=0, description="Learning rate."),
    ]
    model: Annotated[
        Literal["small", "medium", "large"],
        Field(default="medium", description="Model size."),
    ]


def _run_train(cfg: TrainSettings) -> None:
    _setup_logging()
    log.info("Training %s model for %d epochs (lr=%g)", cfg.model, cfg.epochs, cfg.lr)
    for epoch in range(cfg.epochs):
        time.sleep(0.25)
        log.info("epoch %d/%d  loss=%.3f", epoch + 1, cfg.epochs, 1.0 / (epoch + 2))
    log.info("Training complete.")


app = typer.Typer()
add_command(app, DetectSettings, _run_detect, name="detect", config_file=True)
add_command(app, TrainSettings, _run_train, name="train", config_file=True)


def main() -> None:
    """Entry point for the ``typantic-demo`` console script."""
    app()


# Discovery metadata (a plain list of mappings) for the web launcher.
WEB_COMMANDS: list[dict[str, object]] = [
    {
        "app": "typantic-demo",
        "command": "detect",
        "argv": ["detect"],
        "title": "Detect objects",
        "description": "Run a (simulated) detection over images and write overlays.",
        "default_backend": "local",
    },
    {
        "app": "typantic-demo",
        "command": "train",
        "argv": ["train"],
        "title": "Train a model",
        "description": "Train a (simulated) model; streams per-epoch loss to the log.",
        "default_backend": "local",
    },
]
