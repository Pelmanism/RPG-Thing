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
    collider_size: Tuple[int, int] = (18, 12)

    def rect(self) -> pygame.Rect:
        surf = self.sprite._frame_surfaces[0]
        return pygame.Rect(int(self.pos.x), int(self.pos.y), surf.get_width(), surf.get_height())

    def collider_rect(self) -> pygame.Rect:
        # Smaller collider near the entity's "feet" so large ASCII art doesn't make interaction awkward.
        surf = self.sprite._frame_surfaces[0]
        cw, ch = self.collider_size
        cw = min(cw, surf.get_width())
        ch = min(ch, surf.get_height())
        x = self.pos.x + (surf.get_width() - cw) / 2
        y = self.pos.y + surf.get_height() - ch
        return pygame.Rect(int(x), int(y), int(cw), int(ch))


def _circle_near(a: pygame.Vector2, b: pygame.Vector2, radius: float) -> bool:
    return a.distance_to(b) <= radius


# ---------------------------
# ASCII tile map (editable)
# ---------------------------


@dataclass
class AsciiTileMap:
    lines: List[str]
    tile_size: int = 24
    wall_ch: str = "x"
    floor_ch: str = "."
    void_ch: str = " "
    door_ch: str = "D"

    def __post_init__(self) -> None:
        if not self.lines:
            raise ValueError("Map has no lines")
        width = max(len(line) for line in self.lines)
        self.lines = [line.ljust(width, self.void_ch) for line in self.lines]

    @property
    def width(self) -> int:
        return len(self.lines[0])

    @property
    def height(self) -> int:
        return len(self.lines)

    def world_size_px(self) -> Tuple[int, int]:
        return self.width * self.tile_size, self.height * self.tile_size

    def tile_rect(self, tx: int, ty: int) -> pygame.Rect:
        return pygame.Rect(tx * self.tile_size, ty * self.tile_size, self.tile_size, self.tile_size)

    def tile_at(self, tx: int, ty: int) -> str:
        if tx < 0 or ty < 0 or tx >= self.width or ty >= self.height:
            return self.wall_ch
        return self.lines[ty][tx]

    def is_blocking_tile(self, ch: str, gs: GameState) -> bool:
        if ch == self.wall_ch:
            return True
        if ch == self.door_ch and not gs.has("gate_open"):
            return True
        return False

    def rect_collides(self, r: pygame.Rect, gs: GameState) -> bool:
        tx0 = int(math.floor(r.left / self.tile_size))
        ty0 = int(math.floor(r.top / self.tile_size))
        tx1 = int(math.floor((r.right - 1) / self.tile_size))
        ty1 = int(math.floor((r.bottom - 1) / self.tile_size))
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                ch = self.tile_at(tx, ty)
                if self.is_blocking_tile(ch, gs):
                    return True
        return False

    def draw(self, screen: pygame.Surface, camera: pygame.Vector2, gs: GameState) -> None:
        floor_col = (18, 25, 18)
        wall_col = (70, 80, 95)
        void_col = (10, 12, 18)
        door_col = (160, 120, 80)
        door_open_col = (25, 40, 25)

        screen.fill(void_col)

        # Visible tile range
        sw, sh = screen.get_size()
        tx0 = int(_clamp(camera.x / self.tile_size, 0, self.width))
        ty0 = int(_clamp(camera.y / self.tile_size, 0, self.height))
        tx1 = int(_clamp((camera.x + sw) / self.tile_size + 1, 0, self.width))
        ty1 = int(_clamp((camera.y + sh) / self.tile_size + 1, 0, self.height))

        for ty in range(ty0, ty1):
            row = self.lines[ty]
            for tx in range(tx0, tx1):
                ch = row[tx]
                world = self.tile_rect(tx, ty)
                dst = world.move(int(-camera.x), int(-camera.y))
                if ch == self.floor_ch:
                    pygame.draw.rect(screen, floor_col, dst)
                elif ch == self.wall_ch:
                    pygame.draw.rect(screen, wall_col, dst)
                elif ch == self.door_ch:
                    if gs.has("gate_open"):
                        pygame.draw.rect(screen, door_open_col, dst)
                    else:
                        pygame.draw.rect(screen, door_col, dst)
                        pygame.draw.rect(screen, (220, 190, 150), dst.inflate(-8, -8))
                elif ch == self.void_ch:
                    # Leave as void
                    pass
                else:
                    # Treat unknown as floor for easy extension
                    pygame.draw.rect(screen, floor_col, dst)


def parse_map_and_spawns(raw_lines: Sequence[str]) -> Tuple[AsciiTileMap, Dict[str, Tuple[int, int]]]:
    """
    Map legend (editable in-file):
    - x : wall (blocking)
    - . : floor
    - D : door (blocks until gate_open)
    - P : player spawn
    - E : Elder spawn
    - G : Gatekeeper spawn
    """

    lines = [line.rstrip("\n") for line in raw_lines if line.strip("\n") != ""]
    spawns: Dict[str, Tuple[int, int]] = {}
    normalized: List[str] = []
    for y, line in enumerate(lines):
        out = []
        for x, ch in enumerate(line):
            if ch == "P":
                spawns["player"] = (x, y)
                out.append(".")
            elif ch == "E":
                spawns["elder"] = (x, y)
                out.append(".")
            elif ch == "G":
                spawns["gatekeeper"] = (x, y)
                out.append(".")
            else:
                out.append(ch)
        normalized.append("".join(out))
    return AsciiTileMap(normalized), spawns


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

    # Editable ASCII map: tweak these lines to edit your level layout.
    MAP_LINES = [
        "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "x..............................................x",
        "x....P.........................................x",
        "x..............................................x",
        "x..............xxxxxxx.......x.................x",
        "x..............x.....x.......x.................x",
        "x..............x.....x.......x.................x",
        "x..............xxxxxxx.......x.................x",
        "x............................x........G.........",
        "x......E.....................x.................D",
        "x............................x..................",
        "x............................x.................x",
        "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    ]
    tile_map, spawns = parse_map_and_spawns(MAP_LINES)

    world_w, world_h = tile_map.world_size_px()
    screen_w = max(960, min(1280, world_w))
    screen_h = max(540, min(720, world_h))
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

    def pos_from_tile(tile_key: str, sprite: AsciiSprite, fallback: Tuple[int, int]) -> pygame.Vector2:
        tx, ty = spawns.get(tile_key, fallback)
        surf = sprite._frame_surfaces[0]
        x = tx * tile_map.tile_size + (tile_map.tile_size - surf.get_width()) / 2
        y = ty * tile_map.tile_size + (tile_map.tile_size - surf.get_height()) / 2
        return pygame.Vector2(x, y)

    player = Entity(
        "You",
        pos_from_tile("player", player_sprite, (2, 2)),
        player_sprite,
        speed_px_s=150.0,
        solid=True,
    )
    elder = Entity(
        "Elder Kora",
        pos_from_tile("elder", elder_sprite, (8, 4)),
        elder_sprite,
        speed_px_s=0.0,
        solid=True,
        talk_tree=dialogues["elder"],
        interaction_radius=60,
    )
    gatekeeper = Entity(
        "Gatekeeper Bram",
        pos_from_tile("gatekeeper", gate_sprite, (18, 6)),
        gate_sprite,
        speed_px_s=0.0,
        solid=True,
        talk_tree=dialogues["gatekeeper"],
        interaction_radius=70,
    )
    npcs = [elder, gatekeeper]

    active_dialogue: Optional[ActiveDialogue] = None

    def try_move(entity: Entity, delta: pygame.Vector2) -> None:
        if delta.length_squared() == 0:
            return
        old = entity.pos.copy()
        entity.pos.x += delta.x
        if collides(entity.collider_rect(), ignore=entity):
            entity.pos.x = old.x
        entity.pos.y += delta.y
        if collides(entity.collider_rect(), ignore=entity):
            entity.pos.y = old.y

    def collides(r: pygame.Rect, *, ignore: Optional[Entity] = None) -> bool:
        if tile_map.rect_collides(r, gs):
            return True
        for npc in npcs:
            if ignore is npc:
                continue
            if npc.solid and r.colliderect(npc.collider_rect()):
                return True
        if ignore is not player and r.colliderect(player.collider_rect()):
            return True
        return False

    def nearest_talkable() -> Optional[Entity]:
        player_center = pygame.Vector2(player.collider_rect().center)
        for npc in npcs:
            if npc.talk_tree is None:
                continue
            npc_center = pygame.Vector2(npc.collider_rect().center)
            if _circle_near(player_center, npc_center, npc.interaction_radius):
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

        # --- Camera + render world ---
        sw, sh = screen.get_size()
        player_center = pygame.Vector2(player.collider_rect().center)
        cam = player_center - pygame.Vector2(sw / 2, sh / 2)
        max_cam_x = max(0, world_w - sw)
        max_cam_y = max(0, world_h - sh)
        cam.x = _clamp(cam.x, 0, max_cam_x)
        cam.y = _clamp(cam.y, 0, max_cam_y)

        tile_map.draw(screen, cam, gs)

        # NPCs
        for npc in npcs:
            surf = npc.sprite.surface_at(t_s)
            screen.blit(surf, (npc.pos.x - cam.x, npc.pos.y - cam.y))
            name_s = ui_font.render(npc.name, True, (210, 210, 210))
            screen.blit(name_s, (npc.pos.x - cam.x, npc.pos.y - cam.y - 18))

        # Player
        p_surf = player.sprite.surface_at(t_s)
        screen.blit(p_surf, (player.pos.x - cam.x, player.pos.y - cam.y))

        # Interaction prompt
        if active_dialogue is None:
            npc = nearest_talkable()
            if npc:
                prompt = ui_font.render("Press E to talk", True, (240, 240, 200))
                screen.blit(
                    prompt,
                    (player.pos.x - cam.x - 8, player.pos.y - cam.y + p_surf.get_height() + 6),
                )

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
        screen.blit(status_s, (16, 12))

        pygame.display.flip()

        if t_s >= max_time_s:
            running = False

    pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
