package app.azimuthphoto.mobile.ui.library

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.LibraryApi
import app.azimuthphoto.mobile.data.Person
import app.azimuthphoto.mobile.ui.Ink
import app.azimuthphoto.mobile.ui.Panel
import app.azimuthphoto.mobile.ui.TextPrimary
import app.azimuthphoto.mobile.ui.TextSecondary
import coil.compose.AsyncImage
import coil.request.ImageRequest

@Composable
fun PeopleScreen(api: LibraryApi, onBack: () -> Unit, onOpenPerson: (Person) -> Unit) {
    var people by remember { mutableStateOf<List<Person>?>(null) }

    LaunchedEffect(Unit) {
        people = runCatching { api.people() }.getOrDefault(emptyList())
    }

    Column(Modifier.fillMaxSize().background(Ink)) {
        LibraryTopBar(title = "People", onBack = onBack)
        when (val loaded = people) {
            null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(color = TextSecondary)
            }
            else -> LazyVerticalGrid(
                columns = GridCells.Adaptive(96.dp),
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(20.dp),
                horizontalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                items(items = loaded, key = { it.id }) { person ->
                    PersonCard(api = api, person = person, onOpen = { onOpenPerson(person) })
                }
            }
        }
    }
}

@Composable
private fun PersonCard(api: LibraryApi, person: Person, onOpen: () -> Unit) {
    Column(
        modifier = Modifier.clickable(onClick = onOpen),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        AsyncImage(
            model = ImageRequest.Builder(LocalContext.current)
                .data(api.thumb(person.face_thumb_url))
                .crossfade(true)
                .size(256)
                .build(),
            contentDescription = person.displayName,
            contentScale = ContentScale.Crop,
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(1f)
                .clip(CircleShape)
                .background(Panel, CircleShape),
        )
        Text(
            text = person.displayName,
            style = MaterialTheme.typography.bodyMedium,
            color = TextPrimary,
            textAlign = TextAlign.Center,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
        )
        Text(
            text = "${person.photo_count}",
            style = MaterialTheme.typography.labelSmall,
            color = TextSecondary,
            textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth().padding(top = 2.dp),
        )
    }
}
