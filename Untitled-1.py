"""
Prototype ASCII-sprite RPG in pygame.

Controls:
- Move: WASD / Arrow keys
- Interact / advance: E / Space / Enter
- Dialogue: Up/Down + Enter, or number keys
- Close dialogue: Esc / Backspace
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import pygame


# ---------------------------
# ASCII sprite rendering
# ---------------------------


def _render_ascii_lines(
    font: pygame.font.Font,
    lines: Sequence[str],
    color: pygame.Color,
    *,
    alpha: int = 255,
) -> pygame.Surface:
    rendered = [font.render(line, True, color) for line in lines]
    width = max((s.get_width() for s in rendered), default=0)
    height = sum((s.get_height() for s in rendered), 0)
    surf = pygame.Surface((width, height), pygame.SRCALPHA)
    y = 0
    for s in rendered:
        surf.blit(s, (0, y))
        y += s.get_height()
    if alpha != 255:
        surf.set_alpha(alpha)
    return surf


@dataclass(frozen=True)
class AsciiAnim:
    frames: Tuple[Tuple[str, ...], ...]
    color: pygame.Color
    frame_time_s: float = 0.18


@dataclass
class AsciiSprite:
    anim: AsciiAnim
    _frame_surfaces: List[pygame.Surface] = field(default_factory=list, init=False)

    def bake(self, font: pygame.font.Font) -> None:
        self._frame_surfaces = [
            _render_ascii_lines(font, frame, self.anim.color) for frame in self.anim.frames
        ]

    def surface_at(self, t_s: float) -> pygame.Surface:
        if not self._frame_surfaces:
            raise RuntimeError("Sprite not baked. Call bake(font) first.")
        idx = int(t_s / max(self.anim.frame_time_s, 0.01)) % len(self._frame_surfaces)
        return self._frame_surfaces[idx]


# ---------------------------
# Dialogue system
# ---------------------------


Predicate = Callable[["GameState"], bool]
Effect = Callable[["GameState"], None]


@dataclass(frozen=True)
class DialogueChoice:
    text: str
    next_id: Optional[str] = None
    enabled_if: Optional[Predicate] = None
    effect: Optional[Effect] = None


@dataclass(frozen=True)
class DialogueNode:
    id: str
    text: str
    choices: Tuple[DialogueChoice, ...] = ()
    on_enter: Optional[Effect] = None


@dataclass
class DialogueTree:
    nodes: Dict[str, DialogueNode]
    start_id: str

    def node(self, node_id: str) -> DialogueNode:
        return self.nodes[node_id]


@dataclass
class ActiveDialogue:
    npc_name: str
    tree: DialogueTree
    node_id: str
    selected_idx: int = 0

    def node(self) -> DialogueNode:
        return self.tree.node(self.node_id)


@dataclass
class GameState:
    flags: Dict[str, bool] = field(default_factory=dict)

    def has(self, key: str) -> bool:
        return bool(self.flags.get(key))

    def set(self, key: str, value: bool = True) -> None:
        self.flags[key] = value


# ---------------------------
# Entities / world
# ---------------------------


@dataclass
class Entity:
    name: str
    pos: pygame.Vector2
    sprite: AsciiSprite
    speed_px_s: float = 120.0
    solid: bool = True
    talk_tree: Optional[DialogueTree] = None
    interaction_radius: float = 46.0

    def rect(self) -> pygame.Rect:
        surf = self.sprite._frame_surfaces[0]
        return pygame.Rect(int(self.pos.x), int(self.pos.y), surf.get_width(), surf.get_height())


def _circle_near(a: pygame.Vector2, b: pygame.Vector2, radius: float) -> bool:
    return a.distance_to(b) <= radius


# ---------------------------
# UI helpers
# ---------------------------


def _wrap_text(font: pygame.font.Font, text: str, max_width: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    current: List[str] = []
    for word in words:
        test = " ".join(current + [word]) if current else word
        if font.size(test)[0] <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines or [""]


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


# ---------------------------
# Main game
# ---------------------------


def build_dialogues() -> Dict[str, DialogueTree]:
    def give_coin(gs: GameState) -> None:
        gs.set("got_coin", True)

    def coin_not_yet(gs: GameState) -> bool:
        return not gs.has("got_coin")

    def open_gate(gs: GameState) -> None:
        gs.set("gate_open", True)

    elder = DialogueTree(
        start_id="start",
        nodes={
            "start": DialogueNode(
                id="start",
                text="Ah, a new face in Bitwood. Words have power here—choose yours carefully.",
                choices=(
                    DialogueChoice("Who are you?", next_id="who"),
                    DialogueChoice("Any advice?", next_id="advice"),
                    DialogueChoice("Goodbye.", next_id=None),
                ),
            ),
            "who": DialogueNode(
                id="who",
                text="I am Elder Kora, keeper of old strings and older stories.",
                choices=(
                    DialogueChoice("I’m looking for work.", next_id="work"),
                    DialogueChoice("Back.", next_id="start"),
                ),
            ),
            "advice": DialogueNode(
                id="advice",
                text="Talk to everyone. The world remembers what you do—sometimes even what you almost did.",
                choices=(DialogueChoice("Back.", next_id="start"),),
            ),
            "work": DialogueNode(
                id="work",
                text="Take this coin and speak to the Gatekeeper. He likes proof more than promises.",
                choices=(
                    DialogueChoice(
                        "Thanks.",
                        next_id="start",
                        enabled_if=coin_not_yet,
                        effect=give_coin,
                    ),
                    DialogueChoice("I already have it.", next_id="start", enabled_if=lambda gs: gs.has("got_coin")),
                ),
            ),
        },
    )

    gatekeeper = DialogueTree(
        start_id="start",
        nodes={
            "start": DialogueNode(
                id="start",
                text="Halt! The north path is closed. Too many slimes, not enough heroes.",
                choices=(
                    DialogueChoice("Elder sent me.", next_id="proof"),
                    DialogueChoice("Why closed?", next_id="why"),
                    DialogueChoice("Goodbye.", next_id=None),
                ),
            ),
            "why": DialogueNode(
                id="why",
                text="A nest stirred up beyond the gate. I can’t open it for every wanderer.",
                choices=(DialogueChoice("Back.", next_id="start"),),
            ),
            "proof": DialogueNode(
                id="proof",
                text="You got proof? Or just big adventurer energy?",
                choices=(
                    DialogueChoice(
                        "Here’s a coin from Elder Kora.",
                        next_id="open",
                        enabled_if=lambda gs: gs.has("got_coin") and not gs.has("gate_open"),
                    ),
                    DialogueChoice("I don’t have proof.", next_id="start", enabled_if=lambda gs: not gs.has("got_coin")),
                    DialogueChoice("The gate is already open.", next_id="start", enabled_if=lambda gs: gs.has("gate_open")),
                ),
            ),
            "open": DialogueNode(
                id="open",
                text="All right, that’s Kora’s mark. I’ll open it. Try not to get pixelated out there.",
                choices=(DialogueChoice("On my way.", next_id=None, effect=open_gate),),
            ),
        },
    )

    return {"elder": elder, "gatekeeper": gatekeeper}


def main() -> int:
    smoke_test = "--smoke-test" in sys.argv
    if smoke_test:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

    pygame.init()
    pygame.display.set_caption("ASCII RPG Prototype")

    screen_w, screen_h = 960, 540
    screen = pygame.display.set_mode((screen_w, screen_h))
    clock = pygame.time.Clock()

    font = pygame.font.SysFont("Consolas", 18)
    ui_font = pygame.font.SysFont("Consolas", 16)

    # Player and NPC ASCII animations
    player_anim = AsciiAnim(
        color=pygame.Color(230, 230, 255),
        frame_time_s=0.14,
        frames=(
            (
                r"  /\  ",
                r" (..) ",
                r" /||\ ",
                r"  /\  ",
            ),
            (
                r"  /\  ",
                r" (..) ",
                r" /||\ ",
                r"  \/  ",
            ),
        ),
    )
    elder_anim = AsciiAnim(
        color=pygame.Color(255, 220, 160),
        frame_time_s=0.25,
        frames=(
            (
                r"  __  ",
                r" (oo) ",
                r"/|__|\ ",
                r" /  \ ",
            ),
            (
                r"  __  ",
                r" (oO) ",
                r"/|__|\ ",
                r" /  \ ",
            ),
        ),
    )
    gate_anim = AsciiAnim(
        color=pygame.Color(170, 255, 170),
        frame_time_s=0.22,
        frames=(
            (
                r" [==] ",
                r" (..)",
                r" /||\ ",
                r"  / \ ",
            ),
            (
                r" [==] ",
                r" (..)",
                r" /||\ ",
                r"  \ / ",
            ),
        ),
    )

    dialogues = build_dialogues()
    gs = GameState()

    player_sprite = AsciiSprite(player_anim)
    elder_sprite = AsciiSprite(elder_anim)
    gate_sprite = AsciiSprite(gate_anim)
    for s in (player_sprite, elder_sprite, gate_sprite):
        s.bake(font)

    player = Entity("You", pygame.Vector2(180, 260), player_sprite, speed_px_s=150.0, solid=True)
    elder = Entity(
        "Elder Kora",
        pygame.Vector2(160, 120),
        elder_sprite,
        speed_px_s=0.0,
        solid=True,
        talk_tree=dialogues["elder"],
        interaction_radius=60,
    )
    gatekeeper = Entity(
        "Gatekeeper Bram",
        pygame.Vector2(650, 230),
        gate_sprite,
        speed_px_s=0.0,
        solid=True,
        talk_tree=dialogues["gatekeeper"],
        interaction_radius=70,
    )
    npcs = [elder, gatekeeper]

    # Simple obstacles + a "gate" that can open
    obstacles: List[pygame.Rect] = [
        pygame.Rect(0, 0, screen_w, 24),
        pygame.Rect(0, 0, 24, screen_h),
        pygame.Rect(0, screen_h - 24, screen_w, 24),
        pygame.Rect(screen_w - 24, 0, 24, screen_h),
        pygame.Rect(320, 120, 180, 26),
        pygame.Rect(320, 146, 26, 180),
        pygame.Rect(470, 146, 26, 180),
    ]
    gate_rect = pygame.Rect(820, 150, 26, 240)  # blocks path north until opened

    active_dialogue: Optional[ActiveDialogue] = None

    def try_move(entity: Entity, delta: pygame.Vector2) -> None:
        if delta.length_squared() == 0:
            return
        old = entity.pos.copy()
        entity.pos.x += delta.x
        if collides(entity.rect()):
            entity.pos.x = old.x
        entity.pos.y += delta.y
        if collides(entity.rect()):
            entity.pos.y = old.y

    def collides(r: pygame.Rect) -> bool:
        for ob in obstacles:
            if r.colliderect(ob):
                return True
        if not gs.has("gate_open") and r.colliderect(gate_rect):
            return True
        for npc in npcs:
            if npc.solid and r.colliderect(npc.rect()):
                return True
        return False

    def nearest_talkable() -> Optional[Entity]:
        for npc in npcs:
            if npc.talk_tree is None:
                continue
            if _circle_near(player.pos, npc.pos, npc.interaction_radius):
                return npc
        return None

    def open_dialogue(npc: Entity) -> None:
        nonlocal active_dialogue
        active_dialogue = ActiveDialogue(npc_name=npc.name, tree=npc.talk_tree, node_id=npc.talk_tree.start_id)
        node = active_dialogue.node()
        if node.on_enter:
            node.on_enter(gs)
        active_dialogue.selected_idx = 0

    def close_dialogue() -> None:
        nonlocal active_dialogue
        active_dialogue = None

    def enabled_choices(d: ActiveDialogue) -> List[DialogueChoice]:
        choices: List[DialogueChoice] = []
        for c in d.node().choices:
            if c.enabled_if is None or c.enabled_if(gs):
                choices.append(c)
        return choices

    def advance_choice(d: ActiveDialogue, choice: DialogueChoice) -> None:
        nonlocal active_dialogue
        if choice.effect:
            choice.effect(gs)
        if choice.next_id is None:
            active_dialogue = None
            return
        d.node_id = choice.next_id
        node = d.node()
        if node.on_enter:
            node.on_enter(gs)
        d.selected_idx = 0

    running = True
    t_s = 0.0
    max_time_s = 0.35 if smoke_test else float("inf")
    while running:
        dt_s = clock.tick(60) / 1000.0
        t_s += dt_s
        interact_pressed = False
        close_pressed = False
        nav_delta = 0
        choose_number: Optional[int] = None

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_e, pygame.K_SPACE, pygame.K_RETURN):
                    interact_pressed = True
                elif event.key in (pygame.K_ESCAPE, pygame.K_BACKSPACE):
                    close_pressed = True
                elif event.key == pygame.K_UP:
                    nav_delta = -1
                elif event.key == pygame.K_DOWN:
                    nav_delta = 1
                elif pygame.K_1 <= event.key <= pygame.K_9:
                    choose_number = event.key - pygame.K_1

        keys = pygame.key.get_pressed()

        if active_dialogue is None:
            move = pygame.Vector2(0, 0)
            if keys[pygame.K_a] or keys[pygame.K_LEFT]:
                move.x -= 1
            if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
                move.x += 1
            if keys[pygame.K_w] or keys[pygame.K_UP]:
                move.y -= 1
            if keys[pygame.K_s] or keys[pygame.K_DOWN]:
                move.y += 1
            if move.length_squared() > 0:
                move = move.normalize() * player.speed_px_s * dt_s
                try_move(player, move)

            if interact_pressed:
                npc = nearest_talkable()
                if npc:
                    open_dialogue(npc)

        else:
            # Dialogue navigation
            choices = enabled_choices(active_dialogue)
            if nav_delta:
                active_dialogue.selected_idx += nav_delta
            if choices:
                active_dialogue.selected_idx %= len(choices)
            else:
                active_dialogue.selected_idx = 0

            # Number hotkeys (1-9)
            if choose_number is not None and 0 <= choose_number < len(choices):
                advance_choice(active_dialogue, choices[choose_number])
            elif interact_pressed and choices:
                advance_choice(active_dialogue, choices[active_dialogue.selected_idx])
            elif close_pressed and active_dialogue:
                close_dialogue()

        # --- Render world ---
        screen.fill((14, 16, 22))

        # Ground tiles (simple color bands)
        pygame.draw.rect(screen, (18, 25, 18), (24, 24, screen_w - 48, screen_h - 48))
        pygame.draw.rect(screen, (18, 18, 28), (500, 80, 420, 260))

        # Obstacles
        for ob in obstacles:
            pygame.draw.rect(screen, (60, 70, 80), ob)
        if not gs.has("gate_open"):
            pygame.draw.rect(screen, (120, 90, 60), gate_rect)
            pygame.draw.rect(screen, (200, 160, 120), gate_rect.inflate(-8, -8))
        else:
            pygame.draw.rect(screen, (40, 70, 40), gate_rect)

        # North path hint
        pygame.draw.rect(screen, (30, 30, 30), (820, 24, 116, 110))
        hint = ui_font.render("NORTH", True, (220, 220, 220))
        screen.blit(hint, (840, 60))

        # NPCs
        for npc in npcs:
            surf = npc.sprite.surface_at(t_s)
            screen.blit(surf, (npc.pos.x, npc.pos.y))
            name_s = ui_font.render(npc.name, True, (210, 210, 210))
            screen.blit(name_s, (npc.pos.x, npc.pos.y - 18))

        # Player
        p_surf = player.sprite.surface_at(t_s)
        screen.blit(p_surf, (player.pos.x, player.pos.y))

        # Interaction prompt
        if active_dialogue is None:
            npc = nearest_talkable()
            if npc:
                prompt = ui_font.render("Press E to talk", True, (240, 240, 200))
                screen.blit(prompt, (player.pos.x - 8, player.pos.y + p_surf.get_height() + 6))

        # --- Render dialogue UI ---
        if active_dialogue is not None:
            box_h = 170
            box = pygame.Rect(24, screen_h - box_h - 24, screen_w - 48, box_h)
            pygame.draw.rect(screen, (10, 10, 14), box)
            pygame.draw.rect(screen, (90, 100, 120), box, 2)

            title = ui_font.render(active_dialogue.npc_name, True, (255, 240, 200))
            screen.blit(title, (box.x + 12, box.y + 10))

            node = active_dialogue.node()
            text_lines = _wrap_text(ui_font, node.text, box.w - 24)
            y = box.y + 34
            for line in text_lines[:4]:
                screen.blit(ui_font.render(line, True, (220, 220, 230)), (box.x + 12, y))
                y += 18

            choices = enabled_choices(active_dialogue)
            y += 4
            for i, c in enumerate(choices[:6]):
                prefix = f"{i+1}. "
                label = prefix + c.text
                is_sel = i == active_dialogue.selected_idx
                color = (255, 255, 255) if is_sel else (185, 190, 200)
                screen.blit(ui_font.render(label, True, color), (box.x + 12, y))
                y += 18

            help_text = ui_font.render("Up/Down, Enter (or 1-9). Esc to close.", True, (150, 160, 180))
            screen.blit(help_text, (box.x + 12, box.bottom - 26))

        # Debug quest status (tiny)
        status = []
        status.append("coin" if gs.has("got_coin") else "no-coin")
        status.append("gate-open" if gs.has("gate_open") else "gate-closed")
        status_s = ui_font.render(" / ".join(status), True, (120, 130, 150))
        screen.blit(status_s, (28, 28))

        pygame.display.flip()

        if t_s >= max_time_s:
            running = False

    pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
